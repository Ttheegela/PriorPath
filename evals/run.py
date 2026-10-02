"""Score the rule engine against labeled synthetic claims; exit non-zero when a CI gate fails."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.ingest.fhir import claim_to_eob, parse_fhir
from app.models import Severity
from app.reference.base import Reference
from app.reference.normalized import load_normalized
from app.rules import run_rules
from evals.generate import PLANTABLE, LabeledClaim, generate

SCORED = (Severity.ERROR, Severity.OUTLIER)


@dataclass
class RuleScore:
    rule_id: str
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0

    @property
    def support(self) -> int:
        return self.tp + self.fn


@dataclass
class Report:
    scores: dict[str, RuleScore] = field(default_factory=lambda: {r: RuleScore(r) for r in PLANTABLE})
    claims: int = 0
    clean_claims: int = 0
    clean_fp: int = 0
    parse_errors: int = 0

    def gate_failures(self, min_support: int) -> list[str]:
        out = []
        for s in self.scores.values():
            if s.precision < 1.0 or s.recall < 1.0:
                out.append(f"{s.rule_id}: precision {s.precision:.3f}, recall {s.recall:.3f} (gate 1.000)")
            if s.support < min_support:
                out.append(f"{s.rule_id}: support {s.support} < {min_support}")
        if self.clean_fp:
            out.append(f"clean claims with flags: {self.clean_fp} (gate 0)")
        if self.parse_errors:
            out.append(f"FHIR parse errors: {self.parse_errors} (gate 0)")
        return out

    def to_markdown(self) -> str:
        rows = ["| Rule | Support | TP | FP | FN | Precision | Recall |", "|---|---|---|---|---|---|---|"]
        rows += [
            f"| {s.rule_id} | {s.support} | {s.tp} | {s.fp} | {s.fn} | {s.precision:.3f} | {s.recall:.3f} |"
            for s in self.scores.values()
        ]
        rows.append("")
        rows.append(
            f"Claims: {self.claims} (clean: {self.clean_claims}, clean with flags: {self.clean_fp}); "
            f"FHIR parse errors: {self.parse_errors}"
        )
        return "\n".join(rows)


def evaluate(labeled: list[LabeledClaim], ref: Reference, via_fhir: bool = True) -> Report:
    report = Report()
    for lc in labeled:
        report.claims += 1
        claim = lc.claim
        if via_fhir:
            parsed = parse_fhir(claim_to_eob(claim))
            report.parse_errors += len(parsed.errors)
            if not parsed.claims:
                continue
            claim = parsed.claims[0]
        got = {(f.rule_id, frozenset(f.line_ids)) for f in run_rules(claim, ref) if f.severity in SCORED}
        for rule_id, score in report.scores.items():
            exp = {e for e in lc.expected if e[0] == rule_id}
            hit = {g for g in got if g[0] == rule_id}
            score.tp += len(exp & hit)
            score.fp += len(hit - exp)
            score.fn += len(exp - hit)
        if not lc.expected:
            report.clean_claims += 1
            report.clean_fp += 1 if got else 0
    return report


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=300)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--ref", type=Path, default=Path("data/reference/subset"))
    p.add_argument("--out", type=Path, default=Path("evals/results"))
    p.add_argument("--min-support", type=int, default=10)
    a = p.parse_args()

    ref = load_normalized(a.ref)
    report = evaluate(generate(ref, a.n, a.seed), ref)
    a.out.mkdir(parents=True, exist_ok=True)
    summary = {
        "n": a.n,
        "seed": a.seed,
        "reference": [v.ref_version for v in ref.versions],
        "claims": report.claims,
        "clean_claims": report.clean_claims,
        "clean_fp": report.clean_fp,
        "parse_errors": report.parse_errors,
        "rules": {
            k: {**asdict(s), "precision": s.precision, "recall": s.recall} for k, s in report.scores.items()
        },
    }
    (a.out / "latest.json").write_text(json.dumps(summary, indent=2) + "\n")
    (a.out / "latest.md").write_text(report.to_markdown() + "\n")
    print(report.to_markdown())
    failures = report.gate_failures(a.min_support)
    for f in failures:
        print(f"GATE FAILED: {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
