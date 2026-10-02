from fastapi import APIRouter

from app.api.deps import WorkspaceDep

router = APIRouter()


@router.get("/api/workspace")
def get_workspace(ws: WorkspaceDep) -> dict[str, str]:
    return {"id": str(ws.id), "created_at": ws.created_at.isoformat()}
