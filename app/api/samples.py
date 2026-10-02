import json
from functools import lru_cache

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services.demo import DEMO_CASES

router = APIRouter()


@lru_cache(maxsize=1)
def _sample_bundle() -> dict[str, object]:
    bundle = json.loads(DEMO_CASES.read_text())
    entries = bundle["entry"]
    eobs = [e for e in entries if e.get("resource", {}).get("resourceType") == "ExplanationOfBenefit"]
    eobs = eobs[:2]
    return {"resourceType": "Bundle", "type": "collection", "entry": eobs}


@router.get("/api/samples/claim.json")
def sample_claim() -> JSONResponse:
    return JSONResponse(
        _sample_bundle(),
        headers={"Content-Disposition": 'attachment; filename="priorpath-sample-claim.json"'},
    )
