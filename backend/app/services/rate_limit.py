"""Minimal in-process fixed-window rate limiter.

Dependency-free (no slowapi/redis) — sufficient for the single-worker default
deployment. For multi-worker/replicated setups, front this with a shared store
or a reverse-proxy limiter; per-process windows are best-effort there.
"""

import threading
import time

from fastapi import HTTPException, Request

_lock = threading.Lock()
_hits: dict[str, list[float]] = {}


def _client_id(request: Request) -> str:
    """Peer address as seen by the ASGI server.

    X-Forwarded-For is deliberately NOT parsed here: the caller controls it, so
    keying on it lets anyone reset their own bucket per request. Behind a
    reverse proxy, run uvicorn with --proxy-headers --forwarded-allow-ips so the
    server rewrites request.client from the trusted proxy's header instead.
    """
    return request.client.host if request.client else "unknown"


def _check(bucket: str, limit: int, window: float = 60.0) -> None:
    now = time.monotonic()
    cutoff = now - window
    with _lock:
        timestamps = [t for t in _hits.get(bucket, []) if t > cutoff]
        if len(timestamps) >= limit:
            # Keep the trimmed list so we don't lose the window on rejection.
            _hits[bucket] = timestamps
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Try again shortly.",
            )
        timestamps.append(now)
        _hits[bucket] = timestamps
        # Opportunistic sweep so idle clients' buckets don't accumulate forever.
        if len(_hits) > 2048:
            for key in [k for k, v in _hits.items() if not v or v[-1] <= cutoff]:
                del _hits[key]


def rate_limit(name: str, limit, window: float = 60.0):
    """Build a FastAPI dependency enforcing `limit` requests per `window` seconds.

    `limit` may be an int or a zero-arg callable resolved at request time (so the
    bound value tracks config/test overrides instead of freezing at import).
    """

    def dependency(request: Request) -> None:
        resolved = limit() if callable(limit) else limit
        _check(f"{name}:{_client_id(request)}", resolved, window)

    return dependency


def limit_failures(
    name: str, request: Request, limit: int, window: float = 60.0
) -> None:
    """Count one failed attempt for the caller and raise 429 once over `limit`.

    For guards that must stay unlimited on success (admin operations the UI
    polls) but must not be brute-forceable.
    """
    _check(f"{name}:{_client_id(request)}", limit, window)


def reset() -> None:
    """Clear all counters — for tests."""
    with _lock:
        _hits.clear()
