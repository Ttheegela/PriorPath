from fastapi import FastAPI

from app import __version__
from app.api import cases, demo, flags, letters, workspace
from app.api.deps import get_reference

app = FastAPI(
    title="PriorPath",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)
app.include_router(workspace.router)
app.include_router(cases.router)
app.include_router(flags.router)
app.include_router(letters.router)
app.include_router(demo.router)


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
