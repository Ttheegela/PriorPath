import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api import audit_log, cases, demo, flags, letters, samples, workspace
from app.api.deps import get_reference
from app.db.session import get_engine

log = logging.getLogger(__name__)

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
app.include_router(audit_log.router)
app.include_router(samples.router)


@app.get("/api/health")
def health() -> JSONResponse:
    try:
        with get_engine().connect() as conn, conn.begin():
            conn.execute(text("SET LOCAL statement_timeout = '2s'"))
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        log.exception("health check: database unavailable")
        return JSONResponse({"status": "degraded", "db": "unavailable"}, status_code=503)
    reference = [v.ref_version for v in get_reference().versions]
    return JSONResponse({"status": "ok", "db": "ok", "reference": reference})


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


def mount_frontend(target: FastAPI, directory: Path) -> None:
    """Serve the built UI as low-priority routes; skipped when there is no build (tests, API-only dev)."""
    if directory.is_dir():
        target.frontend("/", directory=directory, fallback="index.html")


mount_frontend(app, Path(__file__).resolve().parent.parent / "public")
