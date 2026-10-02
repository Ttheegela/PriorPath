"""Regenerate samples/test-bills/: 6 synthetic PDF bills (3 layouts x clean/scanned), FHIR JSON, answer key.

PYTHONPATH=. python scripts/make_test_bills.py

Deterministic: claims from evals.generate (seed 7), PDFs from evals.pdf_render (fixed header identifiers).
"""

import json
from pathlib import Path

from app.ingest.fhir import claims_to_bundle
from app.models import Severity
from app.reference.normalized import load_normalized
from app.rules import run_rules
from evals.generate import LabeledClaim, generate
from evals.pdf_render import add_scan_noise, render_bill

OUT = Path("samples/test-bills")
PATIENT = "Alex Example"
# (layout, scanned, planted rules wanted); a claim is the first generated one with exactly these plants.
SPECS = [
    ("table", False, ["R3"]),
    ("statement", False, ["R5"]),
    ("compact", False, ["R2"]),
    ("table", True, ["R1"]),
    ("statement", True, ["R3", "R4"]),
    ("compact", True, ["R2"]),
]
REDACTED = (
    "Alex Example (patient name), 1200 Maple Avenue, Springfield, IL 62704 (address), "
    "(217) 555-0143 (phone), XQH4471029 (member ID)"
)
SCANNED_NOTE = "scanned page, so nothing is blacked out (not redactable); identifiers are sent as they are"


def pick(pool: list[LabeledClaim], planted: list[str], used: set[str]) -> LabeledClaim:
    for lc in pool:
        if lc.planted == planted and lc.claim.id not in used and len(lc.claim.lines) <= 9:
            used.add(lc.claim.id)
            return lc
    raise SystemExit(f"no generated claim with plants {planted}")


def main() -> None:
    ref = load_normalized(Path("data/reference/subset"))
    pool = generate(ref, 300, 7)
    used: set[str] = set()
    OUT.mkdir(parents=True, exist_ok=True)
    key = [
        "# PriorPath test bills (synthetic)",
        "",
        "Regenerate with `PYTHONPATH=. python scripts/make_test_bills.py`.",
        "Upload each PDF with the synthetic-bill box ticked.",
        "Every bill header carries the same fake identifiers: " + REDACTED + ".",
        "",
    ]
    first = None
    for i, (layout, scanned, planted) in enumerate(SPECS, 1):
        lc = pick(pool, planted, used)
        first = first or lc
        fired = sorted(
            {f.rule_id for f in run_rules(lc.claim, ref) if f.severity in (Severity.ERROR, Severity.OUTLIER)}
        )
        assert fired == planted, (lc.claim.id, fired, planted)
        pdf = render_bill(lc.claim, layout, PATIENT)
        name = f"TEST-{i:02d}-{layout}{'-scanned' if scanned else ''}.pdf"
        (OUT / name).write_bytes(add_scan_noise(pdf, 100 + i) if scanned else pdf)
        blacked = (
            SCANNED_NOTE
            if scanned
            else "the four identifiers above are blacked out; codes, units, charges and dates stay"
        )
        key.append(
            f"- **{name}**: {len(lc.claim.lines)} lines; rules: {', '.join(planted)}. Redaction: {blacked}."
        )
    assert first
    (OUT / "sample-claims-fhir.json").write_text(json.dumps(claims_to_bundle([first.claim]), indent=2) + "\n")
    key += ["- **sample-claims-fhir.json**: the claim from TEST-01 as a FHIR bundle (no AI model); rule: R3."]
    (OUT / "ANSWER_KEY.md").write_text("\n".join(key) + "\n")


if __name__ == "__main__":
    main()
