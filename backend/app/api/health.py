"""Liveness vs readiness, as distinct contracts.

/health — liveness: the process is up. Nothing else. A liveness probe
that checks dependencies causes restart storms when a dependency blips.

/health/ready — readiness, with the architecture decision encoded as HTTP
semantics: Postgres unreachable is a hard 503 (SQL is the source of
truth — the service cannot serve without it); the vector store degrades
instead of failing (ChromaDB is a rebuildable cache — RAG quality drops,
the service does not leave rotation).

This replaces the ACCIDENTAL readiness check: the compose healthcheck
used to probe /api/v1/setup/status, which happened to touch the DB as a
side effect of an unrelated public endpoint — an implicit, undocumented
contract. Now it is explicit.

No auth on either endpoint: orchestrator probes cannot hold credentials,
and the response discloses only ok/degraded/unreachable strings.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

# Hard bound on each dependency check. The engine's connect_timeout covers
# the connect phase; this covers everything (a query wedged mid-execute).
# A stuck check strands at most these two workers — probes keep failing
# FAST instead of pinning a fresh request thread + pool slot every 30s
# until the shared pool starves real traffic. (Docker's healthcheck
# timeout only kills the probe CLIENT, never the in-flight server call.)
_CHECK_TIMEOUT_SECONDS = 5.0
_check_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cma-health")


def _bounded(check) -> None:
    """Run a dependency check with a hard timeout; raises on timeout."""
    _check_executor.submit(check).result(timeout=_CHECK_TIMEOUT_SECONDS)


def _check_database() -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    finally:
        db.close()


def _check_vector_store() -> None:
    from app.services.vector_store import get_events_collection

    # Lazy singleton: the first probe pays ChromaDB client init (~1s) —
    # the compose healthcheck start_period accommodates it; subsequent
    # probes are a cheap count.
    get_events_collection().count()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/health/ready")
def health_ready():
    checks = {"database": "ok", "vector_store": "ok"}

    try:
        _bounded(_check_database)
    except (Exception, FutureTimeoutError):
        logger.warning("Readiness: database unreachable", exc_info=True)
        checks["database"] = "unreachable"
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": checks},
        )

    try:
        _bounded(_check_vector_store)
    except (Exception, FutureTimeoutError):
        logger.warning("Readiness: vector store degraded", exc_info=True)
        checks["vector_store"] = "degraded"

    status = "ok" if checks["vector_store"] == "ok" else "degraded"
    return {"status": status, "checks": checks}
