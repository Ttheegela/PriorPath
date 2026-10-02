import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.llm.cache import explanation_cache_key
from app.models import Evidence, Flag, Severity
from evals.faithfulness import (
    Item,
    OpenRouterJudge,
    build_items,
    gate_failures,
    load_recorded,
    main,
    record,
    replay,
    save_recording,
    score,
)


def _flag(n: int, rule: str = "R1") -> Flag:
    return Flag(
        id=f"F{n}",
        claim_id="C1",
        rule_id=rule,
        severity=Severity.ERROR,
        line_ids=["L1"],
        evidence=Evidence(table="t", ref_version="v", row={"code": f"9921{n}"}),
        est_overcharge=Decimal(n),
        message=f"finding {n}",
    )


def _items(n: int) -> list[Item]:
    return [Item(explanation_cache_key(f), f, f"text {i}") for i, f in enumerate(map(_flag, range(1, n + 1)))]


FAITHFUL = {"unsupported_claims": [], "reason": "ok", "verdict": "faithful"}
UNFAITHFUL = {"unsupported_claims": ["made up"], "reason": "bad", "verdict": "unfaithful"}


def test_all_faithful_passes_gate() -> None:
    items = _items(5)
    r = score(items, {i.key: FAITHFUL for i in items})
    assert (r.faithful, r.judged, r.unjudged, r.rate) == (5, 5, 0, 1.0)
    assert gate_failures(r) == []


def test_unfaithful_and_junk_fail_gate_and_junk_is_unjudged() -> None:
    items = _items(5)
    raw: dict[str, Any] = {i.key: FAITHFUL for i in items}
    raw[items[0].key] = UNFAITHFUL
    raw[items[1].key] = {"verdict": "maybe"}
    r = score(items, raw)
    assert (r.faithful, r.judged, r.unjudged, r.rate) == (3, 4, 1, 0.6)
    assert any("faithfulness" in f for f in gate_failures(r))
    assert [u.key for u in r.unfaithful] == [items[0].key]


def test_one_unfaithful_one_junk_of_ten_is_point_eight() -> None:
    items = _items(10)
    raw: dict[str, Any] = {i.key: FAITHFUL for i in items}
    raw[items[0].key], raw[items[1].key] = UNFAITHFUL, "junk"
    r = score(items, raw)
    assert r.rate == 0.8 and r.unjudged == 1
    assert any("faithfulness" in f for f in gate_failures(r))


def test_replay_of_missing_key_errors(tmp_path: Path) -> None:
    items = _items(2)
    path = tmp_path / "f.json"
    save_recording(path, "m", {items[0].key: FAITHFUL})
    with pytest.raises(KeyError):
        replay(items, path)


def test_record_resumes_and_saves_atomically(tmp_path: Path) -> None:
    items = _items(3)
    path = tmp_path / "c" / "f.json"
    save_recording(path, "m", {items[0].key: FAITHFUL})
    calls: list[str] = []

    def judge(item: Item) -> dict[str, Any]:
        calls.append(item.key)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return UNFAITHFUL

    with pytest.raises(RuntimeError):
        record(items, judge, path, "m")
    assert calls == [items[1].key, items[2].key]  # present key skipped
    saved = load_recorded(path, "m")  # partial progress survived the crash
    assert set(saved) == {items[0].key, items[1].key}
    assert not path.with_suffix(".tmp").exists()
    assert load_recorded(path, "other-model") == {}


def test_unmatched_explanation_key_is_an_error() -> None:
    f = _flag(1)
    items, unmatched = build_items([f], {explanation_cache_key(f): "x", "deadbeef": "y"})
    assert [i.key for i in items] == [explanation_cache_key(f)] and unmatched == ["deadbeef"]


def test_main_replay_missing_recorded_key_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(Path(__file__).resolve().parent.parent)
    out = tmp_path / "faithfulness.md"
    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps({"model": "m", "judgments": {}}))
    assert main(["--replay", str(rec), "--out", str(out)]) == 2  # real demo keys missing from the recording


class _Resp:
    def __init__(self, content: str, finish: str = "stop") -> None:
        msg = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"finish_reason": finish, "message": msg})()]
        self.usage = None


def _judge(content: str, finish: str = "stop") -> tuple[OpenRouterJudge, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def create(**kw: Any) -> _Resp:
        calls.append(kw)
        return _Resp(content, finish)

    j = OpenRouterJudge("k", "m")
    j._client = type(
        "O", (), {"chat": type("Ch", (), {"completions": type("Co", (), {"create": staticmethod(create)})})}
    )()  # type: ignore[assignment]
    return j, calls


def test_judge_request_has_schema_reasoning_and_token_budget() -> None:
    j, calls = _judge(json.dumps(FAITHFUL))
    assert j(_items(1)[0]) == FAITHFUL
    kw = calls[0]
    assert kw["max_tokens"] == 2000 and kw["temperature"] == 0
    assert kw["extra_body"] == {"reasoning": {"effort": "low"}}
    assert kw["response_format"]["json_schema"]["strict"] is True
    assert list(kw["response_format"]["json_schema"]["schema"]["properties"])[-1] == "verdict"


def test_judge_length_finish_errors_and_junk_is_recorded_raw() -> None:
    j, _ = _judge("{}", finish="length")
    with pytest.raises(RuntimeError):
        j(_items(1)[0])
    j, _ = _judge("not json")
    assert j(_items(1)[0]) == {"raw": "not json"}


def test_main_replay_gate_failure_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(Path(__file__).resolve().parent.parent)
    from evals import faithfulness as fa

    items, _ = fa.build_items(
        fa.demo_flags(fa.load_normalized(Path("data/reference/subset"))),
        json.loads(fa.DEMO_EXPLANATIONS.read_text()),
    )
    rec = tmp_path / "rec.json"
    save_recording(rec, "m", {i.key: UNFAITHFUL for i in items})
    assert main(["--replay", str(rec), "--out", str(tmp_path / "o.md")]) == 1
    assert "faithfulness" in capsys.readouterr().out
