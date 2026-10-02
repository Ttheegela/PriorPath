import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import observability
from app.llm.client import OpenRouterClient
from app.llm.vision import OpenRouterVisionClient, VisionError
from app.main import app


class FakeObs:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs
        self.updates: list[dict[str, Any]] = []
        self.ended = 0

    def update(self, **kw: Any) -> None:
        self.updates.append(kw)

    def end(self) -> None:
        self.ended += 1


class FakeLangfuse:
    def __init__(self) -> None:
        self.observations: list[FakeObs] = []
        self.flushed = 0

    def start_observation(self, **kw: Any) -> FakeObs:
        o = FakeObs(kw)
        self.observations.append(o)
        return o

    def flush(self) -> None:
        self.flushed += 1


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeLangfuse:
    f = FakeLangfuse()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr(observability, "_client", None)
    monkeypatch.setattr(observability, "_pending", 0)
    monkeypatch.setattr(observability, "_warned", False)
    monkeypatch.setattr(observability, "_flusher", None)
    monkeypatch.setattr(observability, "_factory", lambda: f)
    return f


def _reply(content: str = "ok", finish: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )


def test_disabled_without_keys_never_builds_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(observability, "_client", None)

    def boom() -> Any:
        raise AssertionError("constructed")

    monkeypatch.setattr(observability, "_factory", boom)
    assert not observability.observability_enabled()
    with observability.trace_llm("x", model="m", kind="explain", metadata={}) as span:
        span.end({"ok": True}, None)
    observability.flush()


def test_generation_has_model_usage_and_only_allowed_metadata(fake: FakeLangfuse) -> None:
    md: dict[str, str | int | float | bool] = {
        "rule_id": "R1",
        "provider": "Clinic",
        "page_no": 2,
        "pages": 3,
        "workspace": "ws-1",
    }
    with observability.trace_llm("explain", model="m1", kind="explain", metadata=md) as span:
        span.end({"ok": True, "finish_reason": "stop", "letter": "Dear Jane"}, {"input": 3, "output": 4})
    (obs,) = fake.observations
    assert obs.kwargs["as_type"] == "generation" and obs.kwargs["model"] == "m1"
    assert obs.kwargs["metadata"] == {"rule_id": "R1", "page_no": 2, "kind": "explain"}
    (upd,) = obs.updates
    assert upd["usage_details"] == {"input": 3, "output": 4}
    assert upd["output"]["ok"] is True and "letter" not in upd["output"]
    assert "latency_ms" in upd["output"]
    assert obs.ended == 1
    assert "Clinic" not in repr(obs.kwargs) + repr(obs.updates)


@pytest.mark.parametrize("broken", ["factory", "start", "update", "end", "flush"])
def test_sdk_failures_are_swallowed(fake: FakeLangfuse, monkeypatch: pytest.MonkeyPatch, broken: str) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("langfuse down")

    if broken == "factory":
        monkeypatch.setattr(observability, "_factory", boom)
    elif broken == "start":
        monkeypatch.setattr(fake, "start_observation", boom)
    elif broken == "update":
        monkeypatch.setattr(FakeObs, "update", boom)
    elif broken == "end":
        monkeypatch.setattr(FakeObs, "end", boom)
    else:
        monkeypatch.setattr(fake, "flush", boom)
    with observability.trace_llm("x", model="m", kind="explain", metadata={}) as span:
        span.end({"ok": True}, None)
    observability.flush()


def test_explain_client_emits_one_generation_with_usage(
    fake: FakeLangfuse, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = OpenRouterClient("k", "m")
    monkeypatch.setattr(c._client.chat.completions, "create", lambda **kw: _reply("Hello."))
    assert c.complete("s", "patient Jane Doe") == "Hello."
    (obs,) = fake.observations
    assert obs.kwargs["metadata"]["prompt_version"] == "explain-v1"
    assert obs.updates[-1]["usage_details"] == {"input": 11, "output": 7}
    assert "Jane" not in repr(obs.kwargs) + repr(obs.updates)


def test_explain_failure_is_traced_and_reraised(fake: FakeLangfuse, monkeypatch: pytest.MonkeyPatch) -> None:
    c = OpenRouterClient("k", "m")

    def fail(**kw: Any) -> Any:
        raise ConnectionError("down")

    monkeypatch.setattr(c._client.chat.completions, "create", fail)
    with pytest.raises(ConnectionError):
        c.complete("s", "u")
    (obs,) = fake.observations
    assert obs.updates[-1]["output"]["ok"] is False
    assert obs.updates[-1]["output"]["error_type"] == "ConnectionError"


def test_vision_client_traces_without_image_or_prompt(
    fake: FakeLangfuse, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = OpenRouterVisionClient("k", "m")
    monkeypatch.setattr(c._client.chat.completions, "create", lambda **kw: _reply('{"lines": []}'))
    assert c.extract(b"\xff\xd8SECRETIMG", {}, "prompt text", page_no=2) == {"lines": []}
    (obs,) = fake.observations
    dump = repr(obs.kwargs) + repr(obs.updates)
    assert "SECRETIMG" not in dump and "base64" not in dump and "prompt text" not in dump
    assert obs.kwargs["metadata"]["prompt_version"] == "extract-v2"
    assert obs.kwargs["metadata"]["page_no"] == 2


def test_vision_failure_traced_once(fake: FakeLangfuse, monkeypatch: pytest.MonkeyPatch) -> None:
    c = OpenRouterVisionClient("k", "m")
    monkeypatch.setattr(c._client.chat.completions, "create", lambda **kw: _reply("nope", "length"))
    with pytest.raises(VisionError):
        c.extract(b"img", {}, "p")
    (obs,) = fake.observations
    assert obs.updates[-1]["output"]["ok"] is False
    assert obs.updates[-1]["output"]["error_type"] == "VisionError"
    assert obs.ended == 1


def test_request_flushes_after_llm_use(fake: FakeLangfuse) -> None:
    with observability.trace_llm("x", model="m", kind="explain", metadata={}) as span:
        span.end({"ok": True}, None)
    TestClient(app).get("/api/health")
    assert fake.flushed == 1
    TestClient(app).get("/api/health")
    assert fake.flushed == 1  # nothing traced since


def _traced() -> None:
    with observability.trace_llm("x", model="m", kind="explain", metadata={}) as span:
        span.end({"ok": True}, None)


def test_slow_flush_is_bounded(fake: FakeLangfuse, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fake, "flush", lambda: time.sleep(10))
    monkeypatch.setattr(observability, "_FLUSH_BUDGET_SECONDS", 0.5)
    _traced()
    t = time.monotonic()
    assert TestClient(app).get("/api/health").status_code == 200
    assert time.monotonic() - t < 3


def test_failing_flush_in_request_is_swallowed(fake: FakeLangfuse, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("down")

    monkeypatch.setattr(fake, "flush", boom)
    _traced()
    assert TestClient(app).get("/api/health").status_code == 200


def test_no_pending_means_no_flush(fake: FakeLangfuse) -> None:
    TestClient(app).get("/api/health")
    assert fake.flushed == 0


def test_warns_once_per_process(
    fake: FakeLangfuse, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("down")

    monkeypatch.setattr(fake, "start_observation", boom)
    for _ in range(3):
        _traced()
    assert len([r for r in caplog.records if "langfuse tracing failed" in r.message]) == 1


def test_sse_stream_flushes_once_after_stream_ends(fake: FakeLangfuse, db: Any) -> None:
    from app.api.deps import get_llm, get_reference
    from tests.api_helpers import sample_claim, upload
    from tests.fakes import FakeLLM
    from tests.helpers import FIXTURE_REF

    c = OpenRouterClient("k", "m")
    monkeypatch_calls: list[int] = []

    def create(**kw: Any) -> SimpleNamespace:
        monkeypatch_calls.append(fake.flushed)
        return _reply("This line repeats another charge for the same service on the same date.")

    c._client.chat.completions.create = create  # type: ignore[method-assign]
    app.dependency_overrides[get_reference] = lambda: FIXTURE_REF
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    try:
        tc = TestClient(app)
        case_id = upload(tc, [sample_claim()]).json()["cases"][0]["id"]
        tc.post(f"/api/cases/{case_id}/audit")
        app.dependency_overrides[get_llm] = lambda: c
        before = fake.flushed
        r = tc.post(f"/api/cases/{case_id}/explain")
        assert "event: done" in r.text
        assert monkeypatch_calls and all(n == before for n in monkeypatch_calls)  # no flush mid-stream
        assert fake.flushed == before + 1 and len(fake.observations) >= 1
    finally:
        app.dependency_overrides.clear()


def test_vision_parse_failure_keeps_usage_and_finish_reason(
    fake: FakeLangfuse, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = OpenRouterVisionClient("k", "m")
    monkeypatch.setattr(c._client.chat.completions, "create", lambda **kw: _reply("not json"))
    with pytest.raises(VisionError):
        c.extract(b"img", {}, "p")
    (obs,) = fake.observations
    assert obs.updates[-1]["usage_details"] == {"input": 11, "output": 7}
    assert obs.updates[-1]["output"]["finish_reason"] == "stop"
    assert obs.updates[-1]["output"]["ok"] is False


def test_flush_is_single_flight(fake: FakeLangfuse, monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    calls: list[int] = []

    def slow() -> None:
        calls.append(1)
        release.wait(5)

    monkeypatch.setattr(fake, "flush", slow)
    monkeypatch.setattr(observability, "_FLUSH_BUDGET_SECONDS", 0.05)
    _traced()
    observability.flush()  # over budget: its worker is still running
    _traced()
    observability.flush()  # skipped, the span stays pending
    assert len(calls) == 1 and observability.has_pending()
    release.set()
    assert observability._flusher is not None
    observability._flusher.join(5)
    observability.flush()
    assert len(calls) == 2 and not observability.has_pending()


def test_client_is_built_once_under_concurrency(fake: FakeLangfuse, monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[int] = []

    def slow_factory() -> FakeLangfuse:
        built.append(1)
        time.sleep(0.05)
        return fake

    monkeypatch.setattr(observability, "_factory", slow_factory)
    with ThreadPoolExecutor(8) as pool:
        clients = list(pool.map(lambda _: observability._get(), range(8)))
    assert built == [1] and all(c is fake for c in clients)
