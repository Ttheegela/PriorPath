import json
from functools import lru_cache

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response

from app.services.demo import DEMO_BILLS, DEMO_CASES

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


@router.get("/api/samples/bill.pdf")
def sample_bill() -> Response:
    bills = sorted(DEMO_BILLS.glob("*.pdf"))
    if not bills:
        raise HTTPException(404, "No sample bill is available in this deployment")
    return Response(
        bills[0].read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="priorpath-sample-bill.pdf"'},
    )
