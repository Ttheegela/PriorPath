import hashlib
import hmac
import os
import time

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

MAX_DB_BYTES = 400 * 1024 * 1024  # stay under the free database tier
NEW_WORKSPACES_PER_CLIENT = 20  # each one is seeded with ~232 KB of demo PDFs
WORKSPACE_WINDOW_SECONDS = 3600
MAX_TRACKED_CLIENTS = 10_000

# ponytail: fixed-window counter per instance, reset on cold start (Vercel Fluid reuses instances, and
# the WAF's 600 req/300 s per IP on /api/ sits in front); a DB-backed counter if that stops holding.
_window_start = 0.0
_new_workspaces: dict[str, int] = {}  # HMAC of client IP -> workspaces created this window


def demo_is_full(session: Session) -> bool:
    return bool(session.scalar(text("select pg_database_size(current_database())")) > MAX_DB_BYTES)


def ensure_capacity(session: Session) -> None:
    if demo_is_full(session):
        raise HTTPException(status_code=503, detail="the demo is full right now; please try again later")


def _client_key(request: Request) -> str:
    """Vercel sets x-real-ip and x-forwarded-for to the real client IP; only an HMAC of it is kept."""
    ip = (
        request.headers.get("x-real-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "")
    )
    return hmac.new(os.environ.get("SESSION_SECRET", "").encode(), ip.encode(), hashlib.sha256).hexdigest()


def ensure_new_workspace_allowed(request: Request) -> None:
    global _window_start
    now = time.monotonic()
    if now - _window_start >= WORKSPACE_WINDOW_SECONDS or len(_new_workspaces) >= MAX_TRACKED_CLIENTS:
        _new_workspaces.clear()
        _window_start = now
    key = _client_key(request)
    _new_workspaces[key] = _new_workspaces.get(key, 0) + 1
    if _new_workspaces[key] > NEW_WORKSPACES_PER_CLIENT:
        raise HTTPException(
            status_code=429, detail="too many new demo sessions from your network; please try again later"
        )
