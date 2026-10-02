"""Best-effort Langfuse tracing. Never sends prompts, completions, images or names: only the allow-listed
metadata below, model id, latency and token usage. Any SDK failure is swallowed (logged once)."""

import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Literal

log = logging.getLogger(__name__)
Scalar = str | int | float | bool
ALLOWED = {"rule_id", "prompt_version", "page_no", "finish_reason", "ok", "error_type", "kind", "latency_ms"}
_TIMEOUT_SECONDS = 3  # SDK HTTP timeout

_client: Any = None
_pending = 0  # spans ended since the last flush (guarded by _lock)
_lock = threading.Lock()
_FLUSH_BUDGET_SECONDS = 2.0  # langfuse's flush() is unbounded (force_flush 30 s + queue joins)
_warned = False
_flusher: threading.Thread | None = None  # the one flush in flight, if any


def _build() -> Any:
    from langfuse import Langfuse

    # LANGFUSE_HOST is deprecated in the SDK in favour of LANGFUSE_BASE_URL; honour both.
    host = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST")
    return Langfuse(base_url=host, timeout=_TIMEOUT_SECONDS)


_factory: Callable[[], Any] = _build


def observability_enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def _warn(exc: Exception) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("langfuse tracing failed (further failures suppressed): %s", type(exc).__name__)


def _get() -> Any:
    global _client
    with _lock:
        if _client is None:
            _client = _factory()
        return _client


def _clean(d: dict[str, Scalar]) -> dict[str, Scalar]:
    return {k: v for k, v in d.items() if k in ALLOWED}


class Span:
    def __init__(self, obs: Any, started: float) -> None:
        self._obs = obs
        self._started = started
        self.ended = False

    def end(self, output_summary: dict[str, Scalar], usage: dict[str, int] | None = None) -> None:
        if self.ended:
            return
        self.ended = True
        if self._obs is None:
            return
        out = _clean(output_summary) | {"latency_ms": int((time.monotonic() - self._started) * 1000)}
        try:
            self._obs.update(output=out, usage_details=usage)
            self._obs.end()
        except Exception as exc:
            _warn(exc)
        global _pending
        with _lock:
            _pending += 1


@contextmanager
def trace_llm(
    name: str,
    *,
    model: str,
    kind: Literal["explain", "extract", "judge"],
    metadata: dict[str, Scalar],
) -> Iterator[Span]:
    started = time.monotonic()
    obs: Any = None
    if observability_enabled():
        try:
            obs = _get().start_observation(
                name=name,
                as_type="generation",
                model=model,
                metadata=_clean({**metadata, "kind": kind}),
            )
        except Exception as exc:
            _warn(exc)
    span = Span(obs, started)
    try:
        yield span
    except Exception as exc:
        span.end({"ok": False, "error_type": type(exc).__name__})
        raise
    span.end({"ok": True})


def has_pending() -> bool:
    return _pending > 0


def flush() -> None:
    """Deliver pending spans; waits at most the budget (the daemon worker may outlive it).
    Single-flight: while a previous flush is still running, spans stay pending for the next request."""
    global _pending, _flusher
    client = _client

    def run() -> None:
        try:
            client.flush()
        except Exception as exc:
            _warn(exc)

    with _lock:
        if _flusher is not None and _flusher.is_alive():
            return
        n, _pending = _pending, 0
        if n == 0 or client is None:
            return
        _flusher = worker = threading.Thread(target=run, daemon=True)
        worker.start()
    worker.join(_FLUSH_BUDGET_SECONDS)
    if worker.is_alive():
        _warn(TimeoutError("flush exceeded budget"))
