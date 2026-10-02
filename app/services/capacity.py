from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

MAX_DB_BYTES = 400 * 1024 * 1024  # stay under the free database tier


def demo_is_full(session: Session) -> bool:
    return bool(session.scalar(text("select pg_database_size(current_database())")) > MAX_DB_BYTES)


def ensure_capacity(session: Session) -> None:
    if demo_is_full(session):
        raise HTTPException(status_code=503, detail="the demo is full right now; please try again later")
