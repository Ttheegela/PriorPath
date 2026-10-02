"""Precomputed, grounded explanations for the demo cases (built by scripts/build_demo.py --explain)."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from app.models import Flag

DEMO_EXPLANATIONS = Path(__file__).resolve().parent.parent.parent / "data" / "demo" / "explanations.json"


def explanation_cache_key(flag: Flag) -> str:
    payload = {
        "rule": flag.rule_id,
        "severity": flag.severity.value,
        "message": flag.message,
        "evidence": flag.evidence.row,
        "overcharge": str(flag.est_overcharge),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=1)
def _load() -> dict[str, str]:
    if not DEMO_EXPLANATIONS.exists():
        return {}
    data = json.loads(DEMO_EXPLANATIONS.read_text())
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def cached_explanation(flag: Flag) -> str | None:
    return _load().get(explanation_cache_key(flag))
