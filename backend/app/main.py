import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agents, alerts, auth, budgets, dashboard, events, providers, setup
from app.api.admin import router as admin_router
from app.api.deps import require_api_key
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.seed import seed_providers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _daily_maintenance() -> None:
    """Background task: runs rollup and retention once per day."""
    import asyncio

    from app.services.retention import prune_old_events
    from app.services.rollup import backfill_rollups, compute_daily_rollups

    await asyncio.sleep(10)  # let startup settle
    while True:
        try:
            db = SessionLocal()
            try:
                backfill_rollups(db)
                compute_daily_rollups(db)  # yesterday by default
                prune_old_events(db)
            finally:
                db.close()
        except Exception:
            logger.exception("Daily maintenance failed")

        await asyncio.sleep(86400)  # 24 hours


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    # Startup
    logger.info("Starting CMA backend...")
    if settings.DATABASE_URL.startswith("sqlite"):
        os.makedirs("data", exist_ok=True)
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified")

    db = SessionLocal()
    try:
        seed_providers(db)
    finally:
        db.close()

    # Start daily rollup & retention background task
    maintenance_task = asyncio.create_task(_daily_maintenance())

    yield

    # Shutdown
    maintenance_task.cancel()
    logger.info("Shutting down CMA backend...")


app = FastAPI(
    title="Cost Monitoring Agent",
    description="Self-hosted observability for multi-agent LLM systems",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.get("/health")
def health():
    return {"status": "ok"}
