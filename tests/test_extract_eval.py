import json
from pathlib import Path
from typing import Any

import pytest

from evals.extract_eval import (
    Metrics,
    build_dataset,
    gate_failures,
    live_reader,
    main,
    replay_reader,
    run_eval,
    save_recording,
)
from evals.generate import generate
from tests.fakes import FakeVision
from tests.helpers import FIXTURE_DIR, FIXTURE_REF

N = 12


def _f(v: Any) -> dict[str, Any]:
    return {"value": v, "confidence": 0.99}


def _truth_reader(labeled, drop_every: int = 0):  # type: ignore[no-untyped-def]
    by_id = {lc.claim.id: lc.claim for lc in labeled}

    def read(key: str, png: bytes) -> dict[str, Any]:
        claim_id, page_no = key.split("/")
        claim = by_id[claim_id]
        lines = claim.lines if page_no == "1" else []
        if drop_every:
            lines = [ln for i, ln in enumerate(lines, start=1) if i % drop_every]
        return {
            "claim_id": claim.id,
            "provider": claim.provider,
            "payer": claim.payer,
            "lines": [
                {
                    "code": _f(ln.code),
                    "modifiers": _f(ln.modifiers),
                    "units": _f(ln.units),
                    "charge": _f(str(ln.charge)),
                    "date_of_service": _f(ln.date_of_service.isoformat()),
                    "place_of_service": _f(ln.place_of_service or ""),
                }
                for ln in lines
            ],
        }

    return read


@pytest.fixture(scope="module")
def dataset():  # type: ignore[no-untyped-def]
    return build_dataset(generate(FIXTURE_REF, N, 11))


def test_perfect_reader_scores_one_and_passes_gates(dataset) -> None:  # type: ignore[no-untyped-def]
    labeled = [lc for lc, _ in dataset]
    m, report = run_eval(dataset, _truth_reader(labeled), FIXTURE_REF)
    assert (m.precision, m.recall, m.f1) == (1.0, 1.0, 1.0) and m.pages >= N
    assert all(s.recall == 1.0 and s.neg_fp == 0 for s in report.scores.values())
    assert gate_failures(m, report) == []


def test_lossy_reader_fails_the_line_f1_gate(dataset) -> None:  # type: ignore[no-untyped-def]
    labeled = [lc for lc, _ in dataset]
    m, report = run_eval(dataset, _truth_reader(labeled, drop_every=5), FIXTURE_REF)
    assert m.f1 < 0.95
    assert any("line F1" in f for f in gate_failures(m, report))


def test_unmatched_extracted_lines_are_false_positives_not_matches(dataset) -> None:  # type: ignore[no-untyped-def]
    labeled = [lc for lc, _ in dataset]
    truth = _truth_reader(labeled)

    def wrong_charge(key: str, png: bytes) -> dict[str, Any]:
        raw = truth(key, png)
        for r in raw["lines"]:
            r["charge"] = _f("1.00")
        return raw

    m, _ = run_eval(dataset, wrong_charge, FIXTURE_REF)
    assert m.tp == 0 and m.pred > 0


class _Truth:
    """Vision client that answers from the truth reader; the PNG identifies the page."""

    def __init__(self, dataset, read) -> None:  # type: ignore[no-untyped-def]
        self.keys = {png: f"{lc.claim.id}/{i}" for lc, pngs in dataset for i, png in enumerate(pngs, 1)}
        self.read = read

    def extract(self, png: bytes, schema: dict[str, Any], prompt: str) -> dict[str, Any]:
        return self.read(self.keys[png], png)  # type: ignore[no-any-return]


def test_record_replay_round_trip(dataset, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    truth = _truth_reader([lc for lc, _ in dataset], drop_every=4)
    pages: dict[str, Any] = {}
    m1, r1 = run_eval(dataset, live_reader(_Truth(dataset, truth), pages), FIXTURE_REF)
    assert len(pages) == m1.pages
    path = tmp_path / "extraction-test.json"
    save_recording(path, "vendor/model", pages)
    assert json.loads(path.read_text())["model"] == "vendor/model"
    m2, r2 = run_eval(dataset, replay_reader(path), FIXTURE_REF)
    assert m1 == m2 and r1.to_markdown() == r2.to_markdown()


def test_replay_missing_key_is_an_error(dataset, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "r.json"
    save_recording(path, "m", {})
    with pytest.raises(KeyError, match="not in recording"):
        run_eval(dataset, replay_reader(path), FIXTURE_REF)


def test_live_reader_uses_the_page_schema_and_prompt() -> None:
    v = FakeVision([{"lines": []}])
    pages: dict[str, Any] = {}
    live_reader(v, pages)("C0001/1", b"png")
    assert pages == {"C0001/1": {"lines": []}} and "place-of-service" in v.calls[0][1]


def test_cli_replay_writes_results_and_gates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ds = build_dataset(generate(FIXTURE_REF, 6, 11))
    pages: dict[str, Any] = {}
    run_eval(ds, live_reader(_Truth(ds, _truth_reader([lc for lc, _ in ds])), pages), FIXTURE_REF)
    path = tmp_path / "extraction-x.json"
    save_recording(path, "vendor/x", pages)
    out = tmp_path / "extraction.md"
    argv = ["--n", "6", "--seed", "11", "--ref", str(FIXTURE_DIR), "--replay", str(path), "--out", str(out)]
    code = main(argv)
    assert code == 0, capsys.readouterr().out
    assert "vendor/x" in out.read_text() and (tmp_path / "extraction.json").exists()


def test_metrics_f1_zero_safe() -> None:
    assert Metrics().f1 == 0.0
