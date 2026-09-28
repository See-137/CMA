import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import (
    agents,
    alerts,
    auth,
    budgets,
    dashboard,
    events,
    providers,
    setup,
)
from app.api.admin import router as admin_router
from app.api.deps import require_api_key, require_metrics_access
from app.config import settings
from app.database import SessionLocal
from app.logging_config import RequestIDMiddleware, setup_logging
from app.metrics import (
    MAINTENANCE_FAILURES,
    MAINTENANCE_LAST_SUCCESS,
    init_metrics,
)
from app.seed import seed_providers

setup_logging()
logger = logging.getLogger(__name__)


def _run_maintenance_job(name: str, fn) -> None:
    """Run one maintenance job; record success timestamp or failure count.

    Failures are logged and swallowed — one broken job must not kill the
    loop or block the jobs after it. (Retention is safe to run after a
    failed rollup: it only prunes dates whose rollups completed.)
    """
    try:
        fn()
        MAINTENANCE_LAST_SUCCESS.labels(job=name).set_to_current_time()
    except Exception:
        MAINTENANCE_FAILURES.labels(job=name).inc()
        logger.exception("Maintenance job %r failed", name)


def _rollup_job() -> None:
    from app.services.rollup import backfill_rollups, compute_daily_rollups

    db = SessionLocal()
    try:
        backfill_rollups(db)
        compute_daily_rollups(db)  # yesterday by default
    finally:
        db.close()


def _retention_job() -> None:
    from app.services.retention import prune_old_events

    db = SessionLocal()
    try:
        prune_old_events(db)
    finally:
        db.close()


def _embed_backfill_job() -> None:
    from app.services.vector_store import (
        backfill_event_embeddings,
        embed_rollups_for_date,
    )

    db = SessionLocal()
    try:
        backfill_event_embeddings(db, batch_size=500)
        embed_rollups_for_date(db)
    finally:
        db.close()


def _maintenance_jobs() -> list[tuple[str, object]]:
    """The maintenance job list — names are a published contract (metric
    labels that dashboards/alerts key on), so tests assert them.

    Each job opens and closes its OWN session: a shared session is NOT
    isolation — a DB-level failure in one job leaves the session in a
    PendingRollbackError state that cascades into every job after it,
    misattributing one failure as three, and pending rows from a
    half-finished job could be committed by a later one.
    """
    return [
        ("rollup", _rollup_job),
        ("retention", _retention_job),
        ("embed_backfill", _embed_backfill_job),
    ]


async def _daily_maintenance() -> None:
    """Background task: runs rollup, retention, and embedding backfill once
    per day, each isolated (own session) and metricized per job."""
    import asyncio

    await asyncio.sleep(10)  # let startup settle
    while True:
        for name, fn in _maintenance_jobs():
            _run_maintenance_job(name, fn)
        await asyncio.sleep(86400)  # 24 hours


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    # Startup
    logger.info("Starting CMA backend...")
    if settings.DATABASE_URL.startswith("sqlite"):
        os.makedirs("data", exist_ok=True)
    # Schema is managed by Alembic — run `alembic upgrade head` (the Docker
    # entrypoint does this automatically; for local dev run it before starting).
    db = SessionLocal()
    try:
        seed_providers(db)
    finally:
        db.close()

    # Start daily rollup & retention background task. Gated so multi-worker
    # deployments can run it on exactly one process (set CMA_ENABLE_MAINTENANCE
    # =false on the others) — the loop is not safe to run concurrently.
    maintenance_task = None
    if settings.ENABLE_MAINTENANCE:
        maintenance_task = asyncio.create_task(_daily_maintenance())
    else:
        logger.info("Daily maintenance disabled (CMA_ENABLE_MAINTENANCE=false)")

    yield

    # Shutdown
    if maintenance_task is not None:
        maintenance_task.cancel()
    logger.info("Shutting down CMA backend...")


app = FastAPI(
    title="Cost Monitoring Agent",
    description="Self-hosted observability for multi-agent LLM systems",
    version="0.1.0",
    lifespan=lifespan,
)

# Outermost (added last below would invert — Starlette wraps in reverse add
# order): RequestID goes first here so it ends up inside CORS, which is fine
# — the contextvar is set before any route or background task runs.
app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    # Auth is a Bearer header, not cookies — credentialed CORS buys nothing and
    # is dangerous if an origin is ever set permissively.
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Password"],
)

# Mount all routers under /api/v1
PREFIX = "/api/v1"

# Public routes (no auth)
app.include_router(setup.router, prefix=PREFIX)
app.include_router(auth.router, prefix=PREFIX)

# Protected routes (require API key)
auth_deps = [Depends(require_api_key)]
app.include_router(events.router, prefix=PREFIX, dependencies=auth_deps)
app.include_router(agents.router, prefix=PREFIX, dependencies=auth_deps)
app.include_router(providers.router, prefix=PREFIX, dependencies=auth_deps)
app.include_router(budgets.router, prefix=PREFIX, dependencies=auth_deps)
app.include_router(alerts.router, prefix=PREFIX, dependencies=auth_deps)
app.include_router(dashboard.router, prefix=PREFIX, dependencies=auth_deps)
app.include_router(admin_router, prefix=PREFIX, dependencies=auth_deps)

# RAG chat + semantic search
from app.api.chat import router as chat_router
from app.api.search import router as search_router

app.include_router(chat_router, prefix=PREFIX, dependencies=auth_deps)
app.include_router(search_router, prefix=PREFIX, dependencies=auth_deps)


# Liveness + readiness (no auth — orchestrator probes hold no credentials)
from app.api.health import router as health_router

app.include_router(health_router)


# Self-observability: HTTP RED baseline via the instrumentator (route
# templating avoids /agents/123-style path cardinality), domain metrics via
# app.metrics. /metrics is auth-gated — it exposes operational detail.
# Known limitation: require_api_key 503s pre-setup, so Prometheus shows the
# target down until the setup wizard completes; each scrape also costs one
# SetupConfig read.
init_metrics()
# Anchored patterns: excluded_handlers compiles regexes matched with
# unanchored re.search — a bare "/metrics" would exclude any future route
# merely containing that substring. Health probes fire every few seconds
# and would drown real traffic in the HTTP metrics.
Instrumentator(
    excluded_handlers=[r"^/metrics$", r"^/health$", r"^/health/ready$"]
).instrument(app)


@app.get("/metrics", dependencies=[Depends(require_metrics_access)])
def metrics() -> Response:
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


# Optional single-port deploy: serve the built React frontend from this process.
# Mounted LAST so every registered route above (API, health, metrics, docs)
# takes precedence; unknown paths fall back to index.html for SPA routing.
if settings.SERVE_STATIC_DIR:
    if not os.path.isdir(settings.SERVE_STATIC_DIR):
        # A missing dist dir (e.g. frontend not built yet) must degrade to
        # API-only, not crash the whole service into a systemd restart loop.
        logger.warning(
            "CMA_SERVE_STATIC_DIR=%r is not a directory — frontend serving "
            "disabled, API-only mode",
            settings.SERVE_STATIC_DIR,
        )
    else:
        from fastapi.staticfiles import StaticFiles
        from starlette.exceptions import (
            HTTPException as _StarletteHTTPException,
        )

        class _SPAStaticFiles(StaticFiles):
            # StaticFiles *raises* HTTPException(404) for unknown paths (it
            # does not return a 404 response) — catch it and serve the app
            # shell so client-side routes survive deep links and refreshes.
            async def get_response(self, path: str, scope):
                try:
                    return await super().get_response(path, scope)
                except _StarletteHTTPException as exc:
                    if exc.status_code == 404:
                        return await super().get_response("index.html", scope)
                    raise

        app.mount(
            "/",
            _SPAStaticFiles(directory=settings.SERVE_STATIC_DIR, html=True),
            name="frontend",
        )
