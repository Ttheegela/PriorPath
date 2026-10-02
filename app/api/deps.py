import logging
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
from app.llm.client import LLMClient, default_client
from app.llm.vision import VisionClient, default_vision_client
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
        from app.services.capacity import ensure_capacity
        from app.services.demo import demo_enabled, seed_demo

        ensure_capacity(session)
        ws = Workspace()
        session.add(ws)
        session.flush()
        if demo_enabled():
            try:
                with session.begin_nested():
                    seed_demo(session, ws, ref)
            except Exception:
                logging.getLogger(__name__).exception("demo seeding failed")
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


def get_llm() -> LLMClient | None:
    return default_client()


LLMDep = Annotated[LLMClient | None, Depends(get_llm)]


def get_vision() -> VisionClient | None:
    return default_vision_client()


VisionDep = Annotated[VisionClient | None, Depends(get_vision)]
