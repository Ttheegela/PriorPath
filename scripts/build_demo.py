"""Build the demo cases (and, with --explain, their grounded explanations).

PYTHONPATH=. python scripts/build_demo.py            # data/demo/cases.json
PYTHONPATH=. python scripts/build_demo.py --explain  # also explanations.json (needs OPENROUTER_API_KEY)
"""

import argparse
import json
import sys
from pathlib import Path

from app.ingest.fhir import claims_to_bundle
from app.llm.cache import DEMO_EXPLANATIONS, explanation_cache_key
from app.llm.client import default_client
from app.llm.explain import explain_flag
from app.models import Severity
from app.reference.normalized import load_normalized
from app.rules import RuleConfig, run_rules
from evals.generate import generate

OUT = Path("data/demo/cases.json")
PROVIDERS = [
    "Riverside Family Medicine",
    "Lakeview Orthopedics",
    "Northgate Imaging Center",
    "Harbor Physical Therapy",
    "Summit Cardiology",
]
EXPLAINED = {Severity.ERROR, Severity.OUTLIER, Severity.LEAD}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--explain", action="store_true")
    a = p.parse_args()
    ref = load_normalized(Path("data/reference/subset"))
    claims = [
        lc.claim.model_copy(
            update={"provider": PROVIDERS[i % len(PROVIDERS)], "payer": "Medicare (synthetic)"}
        )
        for i, lc in enumerate(generate(ref, 10, seed=2026, error_rate=0.9))
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(claims_to_bundle(claims), indent=1) + "\n")
    print(f"wrote {OUT} ({len(claims)} claims)")
    if not a.explain:
        return 0
    llm = default_client()
    if llm is None:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1
    cache: dict[str, str] = {}
    flags = [f for c in claims for f in run_rules(c, ref, RuleConfig()) if f.severity in EXPLAINED]
    for f in flags:
        text = explain_flag(f, llm)
        if text:
            cache[explanation_cache_key(f)] = text
    DEMO_EXPLANATIONS.write_text(json.dumps(dict(sorted(cache.items())), indent=1) + "\n")
    print(f"wrote {DEMO_EXPLANATIONS} ({len(cache)}/{len(flags)} grounded)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
