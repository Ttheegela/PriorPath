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
    md: dict[str, str | int | float | bool] = {"rule_id": "R1", "provider": "Clinic", "pages": 2}
    with observability.trace_llm("explain", model="m1", kind="explain", metadata=md) as span:
        span.end({"ok": True, "finish_reason": "stop", "letter": "Dear Jane"}, {"input": 3, "output": 4})
    (obs,) = fake.observations
    assert obs.kwargs["as_type"] == "generation" and obs.kwargs["model"] == "m1"
    assert obs.kwargs["metadata"] == {"rule_id": "R1", "pages": 2, "kind": "explain"}
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
