"""Score PDF extraction: line-level F1 and end-to-end rule recall, with record/replay of model output.

Replay re-runs our own parsing over raw recorded model JSON, so CI never calls a model.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.ingest.pdf import pdf_page_images
from app.llm.extract import EXTRACT_PROMPT, PAGE_SCHEMA, ExtractionResult, merge, parse_page
from app.llm.vision import OpenRouterVisionClient, VisionClient
from app.models import LineItem
from app.reference.base import Reference
from app.reference.normalized import load_normalized
from evals.generate import LabeledClaim, generate
from evals.pdf_render import LAYOUTS, add_scan_noise, render_bill
from evals.run import Report, evaluate

PageReader = Callable[[str, bytes], dict[str, Any]]  # ("<claim_id>/<page_no>", png) -> raw page JSON
Dataset = list[tuple[LabeledClaim, list[bytes]]]
MIN_F1 = 0.95
MIN_RECALL = 0.90
MIN_SUPPORT = 3


@dataclass
class Metrics:
    tp: int = 0
    pred: int = 0
    true: int = 0
    pages: int = 0
    errors: int = 0  # extracted rows dropped by validation

    @property
    def precision(self) -> float:
        return self.tp / self.pred if self.pred else 0.0

    @property
    def recall(self) -> float:
        return self.tp / self.true if self.true else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def line_key(ln: LineItem) -> tuple[str, int, Any]:
    return (ln.code, ln.units, ln.charge)


def match_lines(true: list[LineItem], pred: list[LineItem]) -> dict[int, str]:
    """Predicted index -> true line id on (code, units, charge); each true line is matched at most once."""
    free: dict[tuple[str, int, Any], list[str]] = {}
    for t in true:
        free.setdefault(line_key(t), []).append(t.id)
    return {i: free[k].pop(0) for i, p in enumerate(pred) if (k := line_key(p)) in free and free[k]}


def build_dataset(labeled: list[LabeledClaim]) -> Dataset:
    out: Dataset = []
    for i, lc in enumerate(labeled):
        pdf = render_bill(lc.claim, LAYOUTS[i % 3])
        if i % 3 == 2:
            pdf = add_scan_noise(pdf, seed=i)
        out.append((lc, pdf_page_images(pdf)))
    return out


def _rebuild(lc: LabeledClaim, ext: ExtractionResult, matched: dict[int, str]) -> LabeledClaim:
    """Same labels, but the claim holds extracted lines; matched lines reuse the true ids."""
    lines = [ln.model_copy(update={"id": matched.get(i, f"X{i + 1}")}) for i, ln in enumerate(ext.lines)]
    return LabeledClaim(lc.claim.model_copy(update={"lines": lines}), lc.expected, lc.planted, lc.negatives)


def run_eval(dataset: Dataset, read: PageReader, ref: Reference) -> tuple[Metrics, Report]:
    m = Metrics()
    rebuilt = []
    for lc, pngs in dataset:
        pages = [parse_page(read(f"{lc.claim.id}/{n}", png), n) for n, png in enumerate(pngs, 1)]
        ext = merge(pages)
        matched = match_lines(lc.claim.lines, ext.lines)
        m.tp += len(matched)
        m.pred += len(ext.lines)
        m.true += len(lc.claim.lines)
        m.pages += len(pngs)
        m.errors += len(ext.errors)
        rebuilt.append(_rebuild(lc, ext, matched))
    # via_fhir=False: a claim with no extracted lines must still count its expected flags as misses
    return m, evaluate(rebuilt, ref, via_fhir=False)


def gate_failures(m: Metrics, report: Report) -> list[str]:
    out = []
    if m.f1 < MIN_F1:
        out.append(f"line F1 {m.f1:.3f} < {MIN_F1}")
    for s in report.scores.values():
        if s.support >= MIN_SUPPORT and s.recall < MIN_RECALL:
            out.append(f"{s.rule_id}: end-to-end recall {s.recall:.3f} < {MIN_RECALL}")
        if s.neg_fp:
            out.append(f"{s.rule_id}: {s.neg_fp} negative plant(s) flagged (gate 0)")
    return out


def live_reader(client: VisionClient, pages: dict[str, Any]) -> PageReader:
    def read(key: str, png: bytes) -> dict[str, Any]:
        pages[key] = raw = client.extract(png, PAGE_SCHEMA, EXTRACT_PROMPT)
        return raw

    return read


def replay_reader(path: Path) -> PageReader:
    pages: dict[str, Any] = json.loads(path.read_text())["pages"]

    def read(key: str, png: bytes) -> dict[str, Any]:
        if key not in pages:
            raise KeyError(f"{key} not in recording {path.name}")
        return pages[key]  # type: ignore[no-any-return]

    return read


def save_recording(path: Path, model: str, pages: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model, "pages": pages}, indent=1, sort_keys=True) + "\n")


def to_markdown(m: Metrics, report: Report, model: str, layouts: Counter[str]) -> str:
    head = [
        f"Model: `{model}`; pages read: {m.pages}; rows dropped by validation: {m.errors}",
        "",
        "| Line metric | Value |",
        "|---|---|",
        f"| True lines | {m.true} |",
        f"| Extracted lines | {m.pred} |",
        f"| Matched (code, units, charge) | {m.tp} |",
        f"| Precision | {m.precision:.3f} |",
        f"| Recall | {m.recall:.3f} |",
        f"| F1 | {m.f1:.3f} (gate {MIN_F1}) |",
        "",
        "End-to-end: rules run on claims rebuilt from extracted lines, scored against the original labels "
        f"(recall gate {MIN_RECALL} for rules with support >= {MIN_SUPPORT}; "
        "negative-plant false positives 0).",
        "",
        f"Layouts: {', '.join(f'{k} {v}' for k, v in sorted(layouts.items()))}",
        "",
    ]
    return "\n".join(head) + report.to_markdown() + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--ref", type=Path, default=Path("data/reference/subset"))
    p.add_argument("--out", type=Path, default=Path("evals/results/extraction.md"))
    p.add_argument("--recorded", type=Path, default=Path("evals/recorded"))
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", metavar="MODEL")
    mode.add_argument("--replay", type=Path, metavar="PATH")
    a = p.parse_args(argv)

    ref = load_normalized(a.ref)
    dataset = build_dataset(generate(ref, a.n, a.seed))
    pages: dict[str, Any] = {}
    if a.record:
        import os

        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            print("--record needs OPENROUTER_API_KEY")
            return 2
        model = a.record
        read = live_reader(OpenRouterVisionClient(key, model), pages)
        path = a.recorded / f"extraction-{model.replace('/', '-')}.json"
    else:
        model = json.loads(a.replay.read_text())["model"]
        read = replay_reader(a.replay)
    try:
        m, report = run_eval(dataset, read, ref)
    finally:
        if a.record:  # keep partial recordings so a failed run is not paid for twice
            save_recording(path, model, pages)
    layouts = Counter(LAYOUTS[i % 3] for i in range(a.n))
    text = to_markdown(m, report, model, layouts)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text)
    a.out.with_suffix(".json").write_text(
        json.dumps({"model": model, "n": a.n, "seed": a.seed, "lines": {**asdict(m), "f1": m.f1}}, indent=2)
        + "\n"
    )
    print(text)
    failures = gate_failures(m, report) if a.replay else []  # gates apply to committed recordings only
    for f in failures:
        print(f"GATE FAILED: {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
