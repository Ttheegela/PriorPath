import hmac
import os

from fastapi import APIRouter, Header, HTTPException

from app.api.deps import RefDep, SessionDep, WorkspaceDep
from app.services.demo import cleanup_old_workspaces, reset_demo

router = APIRouter()


@router.post("/api/demo/reset")
def demo_reset(ws: WorkspaceDep, session: SessionDep, ref: RefDep) -> dict[str, int]:
    count = reset_demo(session, ws, ref)
    session.commit()
    return {"cases": count}


@router.get("/api/internal/cleanup")
def cleanup(session: SessionDep, authorization: str | None = Header(default=None)) -> dict[str, int]:
    secret = os.environ.get("CRON_SECRET", "")
    if not secret or not hmac.compare_digest((authorization or "").encode(), f"Bearer {secret}".encode()):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"deleted": cleanup_old_workspaces(session)}
