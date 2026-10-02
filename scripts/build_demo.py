"""Build the demo cases (and, with --explain, their grounded explanations).

PYTHONPATH=. python scripts/build_demo.py            # data/demo/cases.json
PYTHONPATH=. python scripts/build_demo.py --explain  # also explanations.json (needs OPENROUTER_API_KEY)
PYTHONPATH=. python scripts/build_demo.py --bills    # 2 PDF demo bills (no key; leaves cases.json alone)
PYTHONPATH=. python scripts/build_demo.py --bills --extract [--explain]  # vision extraction (needs key)
"""

import argparse
import json
import os
import sys
from pathlib import Path

from app.ingest.fhir import claims_to_bundle
from app.ingest.pdf import PdfError, pdf_page_images
from app.llm.cache import DEMO_EXPLANATIONS, explanation_cache_key
from app.llm.client import default_client
from app.llm.explain import explain_flag
from app.llm.extract import extract_page, merge
from app.llm.vision import DEFAULT_EXTRACT_MODEL, VisionError, default_vision_client
from app.models import Claim, LineItem, Severity
from app.reference.base import InMemoryReference
from app.reference.normalized import load_normalized
from app.rules import RuleConfig, run_rules
from evals.generate import generate
from evals.pdf_render import add_scan_noise, render_bill

OUT = Path("data/demo/cases.json")
PROVIDERS = [
    "Riverside Family Medicine",
    "Lakeview Orthopedics",
    "Northgate Imaging Center",
    "Harbor Physical Therapy",
    "Summit Cardiology",
]
BILLS = Path("data/demo/bills")
BILL_CLAIMS = Path("data/demo/bill_claims.json")
EXTRACTIONS = Path("data/demo/pdf_extractions.json")
BILL_LAYOUTS = ("table", "statement")  # B0001 clean, B0002 scan-noisy
EXPLAINED = {Severity.ERROR, Severity.OUTLIER, Severity.LEAD}


def _explain(claims: list[Claim], ref: InMemoryReference, merge_existing: bool) -> int:
    llm = default_client()
    if llm is None:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1
    cache: dict[str, str] = {}
    if merge_existing and DEMO_EXPLANATIONS.exists():
        cache = json.loads(DEMO_EXPLANATIONS.read_text())  # existing entries stay untouched
    flags = [f for c in claims for f in run_rules(c, ref, RuleConfig()) if f.severity in EXPLAINED]
    done = 0
    for f in flags:
        key = explanation_cache_key(f)
        if merge_existing and key in cache:
            done += 1
            continue
        text = explain_flag(f, llm)
        if text:
            cache[key] = text
            done += 1
    DEMO_EXPLANATIONS.write_text(json.dumps(dict(sorted(cache.items())), indent=1) + "\n")
    print(f"wrote {DEMO_EXPLANATIONS} ({done}/{len(flags)} grounded)")
    return 0


def _bill_claims(ref: InMemoryReference) -> list[Claim]:
    """Two claims with real errors and the widest mix of severities, from their own seed."""
    scored = []
    for i, lc in enumerate(generate(ref, 12, seed=7001, error_rate=1.0)):
        sev = {f.severity for f in run_rules(lc.claim, ref, RuleConfig())}
        if Severity.ERROR in sev:
            scored.append((-len(sev), i, lc.claim))
    picked = sorted(scored, key=lambda t: t[:2])[: len(BILL_LAYOUTS)]
    return [
        c.model_copy(update={"id": f"B{n:04d}", "provider": PROVIDERS[n], "payer": "Medicare (synthetic)"})
        for n, (_, _, c) in enumerate(picked, start=1)
    ]


def _build_bills(extract: bool, explain: bool, ref: InMemoryReference) -> int:
    claims = _bill_claims(ref)
    BILLS.mkdir(parents=True, exist_ok=True)
    for c, layout in zip(claims, BILL_LAYOUTS, strict=True):
        pdf = render_bill(c, layout)
        BILLS.joinpath(f"{c.id}.pdf").write_bytes(
            add_scan_noise(pdf, seed=7) if layout == "statement" else pdf
        )
    BILL_CLAIMS.write_text(json.dumps([c.model_dump(mode="json") for c in claims], indent=1) + "\n")
    print(f"wrote {len(claims)} bills in {BILLS} and {BILL_CLAIMS}")
    if not extract:
        return 0
    vision = default_vision_client()
    if vision is None:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1
    model = os.environ.get("EXTRACT_MODEL") or DEFAULT_EXTRACT_MODEL
    out: dict[str, dict[str, object]] = {}
    if EXTRACTIONS.exists():
        out = json.loads(EXTRACTIONS.read_text())
    for c in claims:
        if out.get(c.id, {}).get("model") == model:
            print(f"{c.id}: already extracted with {model}; skipping")
            continue
        data = BILLS.joinpath(f"{c.id}.pdf").read_bytes()
        try:
            pages = pdf_page_images(data, redact=True)  # what production sends the model
            m = merge([extract_page(png, n, vision) for n, png in enumerate(pages, start=1)])
        except (VisionError, PdfError) as exc:
            print(
                f"{c.id}: extraction failed ({exc}); saved bills kept, rerun to resume",
                file=sys.stderr,
            )
            return 1
        out[c.id] = {
            "lines": [ln.model_dump(mode="json") for ln in m.lines],
            "errors": m.errors,
            "provider": m.provider,
            "payer": m.payer,
            "model": model,
        }
        tmp = EXTRACTIONS.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, indent=1) + "\n")
        tmp.replace(EXTRACTIONS)  # saved per bill: a later failure keeps this one
        print(f"{c.id}: extracted {len(m.lines)} lines")
    if not explain:
        return 0
    extracted = [
        c.model_copy(
            update={
                "lines": [LineItem.model_validate(ln) for ln in out[c.id]["lines"]],  # type: ignore[attr-defined]
                "provider": out[c.id]["provider"],
                "payer": out[c.id]["payer"],
            }
        )
        for c in claims
    ]
    return _explain(extracted, ref, merge_existing=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--explain", action="store_true")
    p.add_argument("--bills", action="store_true")
    p.add_argument("--extract", action="store_true")
    a = p.parse_args()
    ref = load_normalized(Path("data/reference/subset"))
    if a.bills:
        return _build_bills(a.extract, a.explain, ref)
    if a.extract:
        p.error("--extract needs --bills")
    claims = [
        lc.claim.model_copy(
            update={"provider": PROVIDERS[i % len(PROVIDERS)], "payer": "Medicare (synthetic)"}
        )
        for i, lc in enumerate(generate(ref, 10, seed=2026, error_rate=0.9))
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(claims_to_bundle(claims), indent=1) + "\n")
    print(f"wrote {OUT} ({len(claims)} claims)")
    return _explain(claims, ref, merge_existing=False) if a.explain else 0


if __name__ == "__main__":
    raise SystemExit(main())
