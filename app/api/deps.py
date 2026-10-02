import os
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request, Response
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session

from app.db.models import Workspace
from app.db.session import get_session
from app.reference.base import InMemoryReference
from app.reference.normalized import load_normalized

REF_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "reference" / "subset"
COOKIE_NAME = "pp_ws"
COOKIE_MAX_AGE = 24 * 3600

SessionDep = Annotated[Session, Depends(get_session)]


@lru_cache(maxsize=1)
def get_reference() -> InMemoryReference:
    return load_normalized(REF_DIR)


RefDep = Annotated[InMemoryReference, Depends(get_reference)]


def _serializer() -> URLSafeSerializer:
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        raise RuntimeError("SESSION_SECRET is not set")
    return URLSafeSerializer(secret, salt="workspace")


def _load(session: Session, raw: str) -> Workspace | None:
    try:
        ws_id = uuid.UUID(str(_serializer().loads(raw)))
    except (BadSignature, ValueError):
        return None
    return session.get(Workspace, ws_id)


def current_workspace(request: Request, response: Response, session: SessionDep, ref: RefDep) -> Workspace:
    raw = request.cookies.get(COOKIE_NAME)
    ws = _load(session, raw) if raw else None
    if ws is None:
        ws = Workspace()
        session.add(ws)
        session.commit()
        response.set_cookie(
            COOKIE_NAME,
            _serializer().dumps(str(ws.id)),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=os.environ.get("VERCEL") == "1",
        )
    return ws


WorkspaceDep = Annotated[Workspace, Depends(current_workspace)]
