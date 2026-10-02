"""Faithfulness of the demo explanations: a judge model checks each against its flag's evidence.

--record JUDGE_MODEL calls the judge and saves its raw JSON to evals/recorded/faithfulness.json;
--replay PATH re-scores a recording with no network and gates on it (spec section 8).
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.ingest.fhir import parse_fhir
from app.llm.cache import DEMO_EXPLANATIONS, explanation_cache_key
from app.llm.client import OPENROUTER_BASE_URL, usage_of
from app.llm.rule_text import RULE_TEXT
from app.models import Claim, Flag, Severity
from app.observability import trace_llm
from app.reference.base import Reference
from app.reference.normalized import load_normalized
from app.rules import RuleConfig, run_rules

DEFAULT_JUDGE_MODEL = "google/gemini-2.5-flash"
PROMPT_VERSION = "judge-v2"
MIN_FAITHFUL = 0.90
EXPLAINED = {Severity.ERROR, Severity.OUTLIER, Severity.LEAD}  # as in scripts/build_demo.py
DEMO_DIR = DEMO_EXPLANATIONS.parent
VERDICTS = ("faithful", "unfaithful")
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
        "verdict": {"type": "string", "enum": list(VERDICTS)},
    },
    "required": ["unsupported_claims", "reason", "verdict"],
    "additionalProperties": False,
}
SYSTEM_PROMPT = (
    "Check the EXPLANATION against the facts below. Mark it UNFAITHFUL if it (a) states any number, "
    "dollar amount, date, code or unit that is not in EVIDENCE/FINDING/ESTIMATED OVERCHARGE (arithmetic "
    "derived from them is OK), (b) describes a rule other than the one given or misstates what the rule "
    "means, (c) says or implies fraud, illegality, wrongdoing, intent or certainty that the finding is an "
    "error, when the rule only supports 'may be' / 'worth asking about', (d) recommends specific actions, "
    "legal claims, deadlines or outcomes beyond generic 'ask the provider/payer for clarification'. "
    "Rewording, simplification, and plain-language definitions of the rule and codes are FAITHFUL. "
    "Do not penalise style or omissions. First list each unsupported claim verbatim, then reason, "
    "then verdict."
)
MAX_TOKENS = 2000  # gemini-2.5-flash reasoning tokens count against this
REASONING = {"reasoning": {"effort": "low"}}


@dataclass
class Item:
    key: str  # explanation_cache_key
    flag: Flag
    explanation: str


Judge = Callable[[Item], dict[str, Any]]


def judge_prompt(item: Item) -> str:
    f = item.flag
    return (
        f"RULE ({f.rule_id}): {RULE_TEXT.get(f.rule_id, '')}\nSEVERITY: {f.severity.value}\n"
        f"FINDING: {f.message}\nEVIDENCE: {json.dumps(f.evidence.row, sort_keys=True)}\n"
        f"ESTIMATED OVERCHARGE: ${f.est_overcharge}\n\nEXPLANATION TO CHECK:\n{item.explanation}"
    )


class OpenRouterJudge:
    def __init__(self, api_key: str, model: str, timeout: float = 60.0) -> None:
        self._client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=timeout, max_retries=0)
        self._model = model

    def __call__(self, item: Item) -> dict[str, Any]:
        meta: dict[str, str | int | float | bool] = {
            "prompt_version": PROMPT_VERSION,
            "rule_id": item.flag.rule_id,
        }
        with trace_llm("judge", model=self._model, kind="judge", metadata=meta) as span:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": judge_prompt(item)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "faithfulness", "strict": True, "schema": SCHEMA},
                },
                max_tokens=MAX_TOKENS,
                extra_body=REASONING,
                temperature=0,
            )
            choice = response.choices[0]
            ok = choice.finish_reason == "stop"
            span.end({"ok": ok, "finish_reason": str(choice.finish_reason)}, usage_of(response))
            if not ok:
                raise RuntimeError(f"judge response incomplete: {choice.finish_reason}")
            content = choice.message.content or ""
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                return {"raw": content}  # junk is recorded as-is and scored as unjudged
            return parsed if isinstance(parsed, dict) else {"raw": content}


def demo_flags(ref: Reference, demo_dir: Path = DEMO_DIR) -> list[Flag]:
    """Flags the demo explanations were built for: audit the demo claims with the real subset and config."""
    claims = list(parse_fhir(json.loads((demo_dir / "cases.json").read_text())).claims)
    claims += [Claim.model_validate(c) for c in json.loads((demo_dir / "bill_claims.json").read_text())]
    return [f for c in claims for f in run_rules(c, ref, RuleConfig()) if f.severity in EXPLAINED]


def build_items(flags: list[Flag], explanations: dict[str, str]) -> tuple[list[Item], list[str]]:
    """(items, explanation keys that match no rebuilt flag). Several flags with one key are one item."""
    by_key = {explanation_cache_key(f): f for f in flags}
    items = [Item(k, by_key[k], text) for k, text in sorted(explanations.items()) if k in by_key]
    return items, sorted(k for k in explanations if k not in by_key)


@dataclass
class Result:
    faithful: int
    judged: int
    total: int
    unfaithful: list[Item]
    reasons: dict[str, dict[str, Any]]
    by_rule: dict[str, tuple[int, int]]  # rule -> (faithful, total)

    @property
    def unjudged(self) -> int:
        return self.total - self.judged

    @property
    def rate(self) -> float:
        return self.faithful / self.total if self.total else 0.0  # unjudged counts as not faithful


def _valid(raw: Any) -> bool:
    return (
        isinstance(raw, dict)
        and raw.get("verdict") in VERDICTS
        and isinstance(raw.get("unsupported_claims"), list)
        and isinstance(raw.get("reason"), str)
    )


def score(items: list[Item], raw: dict[str, Any]) -> Result:
    faithful = judged = 0
    unfaithful: list[Item] = []
    reasons: dict[str, dict[str, Any]] = {}
    by_rule: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for it in items:
        by_rule[it.flag.rule_id][1] += 1
        r = raw[it.key]
        if not _valid(r):
            continue
        judged += 1
        if r["verdict"] == "faithful":
            faithful += 1
            by_rule[it.flag.rule_id][0] += 1
        else:
            unfaithful.append(it)
            reasons[it.key] = r
    return Result(
        faithful, judged, len(items), unfaithful, reasons, {k: (a, b) for k, (a, b) in by_rule.items()}
    )


def gate_failures(r: Result) -> list[str]:
    return [f"faithfulness {r.rate:.3f} < {MIN_FAITHFUL}"] if r.rate < MIN_FAITHFUL else []


def load_recorded(path: Path, model: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    rec = json.loads(path.read_text())
    return dict(rec["judgments"]) if rec.get("model") == model else {}


def save_recording(path: Path, model: str, judgments: dict[str, Any]) -> None:
    """Atomic: a crash mid-write never replaces a complete recording."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"model": model, "judgments": judgments}, indent=1, sort_keys=True) + "\n")
    tmp.replace(path)


def record(items: list[Item], judge: Judge, path: Path, model: str) -> dict[str, Any]:
    """Judges items missing from the recording; partial progress is saved even if the judge fails."""
    judgments = load_recorded(path, model)
    try:
        for it in items:
            if it.key not in judgments:
                judgments[it.key] = judge(it)
    finally:
        save_recording(path, model, judgments)
    return judgments


def replay(items: list[Item], path: Path) -> dict[str, Any]:
    judgments: dict[str, Any] = json.loads(path.read_text())["judgments"]
    for it in items:
        if it.key not in judgments:
            raise KeyError(f"{it.key} not in recording {path.name}")
    return judgments


def to_markdown(r: Result, model: str) -> str:
    lines = [
        f"Judge: `{model}`; n={r.total} demo explanations; judged: {r.judged}; "
        f"unjudged (junk output): {r.unjudged}",
        "",
        f"n={r.total} demo explanations; rates are indicative only; one miss moves the rate by "
        f"~{1 / r.total if r.total else 0:.3f}.",
        "",
        f"Faithfulness: **{r.rate:.3f}** ({r.faithful}/{r.total}; gate {MIN_FAITHFUL}). "
        "Unjudged items count as not faithful.",
        "",
        "| Rule | Faithful | n | Rate |",
        "|---|---|---|---|",
        *(f"| {k} | {a} | {b} | {a / b:.3f} |" for k, (a, b) in sorted(r.by_rule.items())),
        "",
        "Rules with no explanations: " + (", ".join(sorted(set(RULE_TEXT) - set(r.by_rule))) or "none"),
        "",
        "## Unfaithful explanations",
        "",
    ]
    if not r.unfaithful:
        lines.append("None.")
    for it in r.unfaithful:
        j = r.reasons[it.key]
        lines += [
            f"- `{it.key[:12]}` {it.flag.rule_id} ({it.flag.claim_id}): {it.explanation}",
            f"  - Unsupported: {'; '.join(j['unsupported_claims']) or 'none listed'}",
            f"  - Reason: {j['reason']}",
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ref", type=Path, default=Path("data/reference/subset"))
    p.add_argument("--out", type=Path, default=Path("evals/results/faithfulness.md"))
    p.add_argument("--recorded", type=Path, default=Path("evals/recorded/faithfulness.json"))
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", metavar="JUDGE_MODEL", nargs="?", const=DEFAULT_JUDGE_MODEL)
    mode.add_argument("--replay", type=Path, metavar="PATH")
    a = p.parse_args(argv)

    explanations: dict[str, str] = json.loads(DEMO_EXPLANATIONS.read_text())
    items, unmatched = build_items(demo_flags(load_normalized(a.ref)), explanations)
    for k in unmatched:
        print(f"ERROR: explanation {k} matches no rebuilt demo flag")
    if unmatched:
        return 2
    if a.record:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            print("--record needs OPENROUTER_API_KEY")
            return 2
        model = a.record
        raw = record(items, OpenRouterJudge(key, model), a.recorded, model)
    else:
        model = json.loads(a.replay.read_text())["model"]
        try:
            raw = replay(items, a.replay)
        except KeyError as e:
            print(f"ERROR: {e.args[0]}")
            return 2
    r = score(items, raw)
    text = to_markdown(r, model)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text)
    a.out.with_suffix(".json").write_text(
        json.dumps(
            {
                "model": model,
                "faithful": r.faithful,
                "judged": r.judged,
                "total": r.total,
                "rate": r.rate,
                "by_rule": {k: {"faithful": a, "n": b} for k, (a, b) in sorted(r.by_rule.items())},
            },
            indent=2,
        )
        + "\n"
    )
    print(text)
    failures = gate_failures(r) if a.replay else []  # gates apply to committed recordings only
    for f in failures:
        print(f"GATE FAILED: {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
