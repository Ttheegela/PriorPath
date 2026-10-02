from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI

from app import __version__
from app.reference.base import InMemoryReference
from app.reference.normalized import load_normalized

REF_DIR = Path(__file__).resolve().parent.parent / "data" / "reference" / "subset"

app = FastAPI(
    title="PriorPath",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)


@lru_cache(maxsize=1)
def get_reference() -> InMemoryReference:
    return load_normalized(REF_DIR)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/version")
def version() -> dict[str, object]:
    ref = get_reference()
    return {
        "version": __version__,
        "reference": [
            {
                "ref_version": v.ref_version,
                "kind": v.kind,
                "valid_from": v.valid_from.isoformat(),
                "valid_to": v.valid_to.isoformat(),
            }
            for v in ref.versions
        ],
    }
