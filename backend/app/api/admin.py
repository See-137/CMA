import logging
import os
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_admin
from app.config import settings
from app.logging_config import LOG_FILE
from app.models.models import Agent, Alert, Budget, CostEvent, SetupConfig
from app.services.cost_engine import reprice_events
from app.timeutils import to_naive_utc, utcnow

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)

# Admin password (not the shared API key) is required for destructive/sensitive
# operations — a leaked ingest token must not be able to wipe or exfiltrate data.
admin_only = [Depends(require_admin)]


@router.get("/status")
def system_status(db: Session = Depends(get_db)):
    """System health and stats."""
    db_size_mb = 0
    if settings.DATABASE_URL.startswith("sqlite"):
        db_path = os.path.join("data", "cma.db")
        if os.path.exists(db_path):
            db_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2)

    config = db.query(SetupConfig).first()

    return {
        "status": "running",
        "version": "0.1.0",
        "deployment_name": config.deployment_name if config else "CMA",
        "uptime_check": utcnow().isoformat(),
        "database": {
            "size_mb": db_size_mb,
            "total_events": db.query(func.count(CostEvent.id)).scalar() or 0,
            "total_agents": db.query(func.count(Agent.id)).scalar() or 0,
            "active_budgets": db.query(func.count(Budget.id))
            .filter(Budget.is_active.is_(True))
            .scalar()
            or 0,
            "unresolved_alerts": db.query(func.count(Alert.id))
            .filter(Alert.is_resolved.is_(False))
            .scalar()
            or 0,
        },
    }


@router.get("/logs", dependencies=admin_only)
def recent_logs():
    """Return recent application log lines (JSON lines from the rotating
    file handler — the path is owned by app.logging_config).

    The read retries once: on Windows, holding a read handle at the exact
    moment the handler rotates makes the rename fail (WinError 32) —
    keeping the read window short and retrying beats colliding twice.
    """
    if not os.path.exists(LOG_FILE):
        return {"lines": ["No log file found yet — nothing has been logged."]}

    for attempt in range(2):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            break
        except OSError:
            if attempt:
                raise
    # Return last 100 lines
    return {"lines": [line.rstrip() for line in lines[-100:]]}


@router.post("/reset", dependencies=admin_only)
def reset_database(db: Session = Depends(get_db)):
    """Clear all data but keep setup config. Returns fresh state."""
    # Delete in order to respect foreign keys
    db.execute(text("DELETE FROM alerts"))
    db.execute(text("DELETE FROM alert_channels"))
    db.execute(text("DELETE FROM cost_events"))
    db.execute(text("DELETE FROM daily_cost_rollups"))
    db.execute(text("DELETE FROM budgets"))
    db.execute(text("DELETE FROM agents"))
    db.commit()

    # Clear vector store so RAG doesn't answer from stale embeddings
    try:
        from app.services.vector_store import clear_vector_store

        clear_vector_store()
    except Exception:
        logger.warning("Failed to clear vector store during reset", exc_info=True)

    return {
        "message": "All monitoring data cleared. Setup config and providers preserved.",
        "cleared": [
            "events",
            "agents",
            "budgets",
            "alerts",
            "alert_channels",
            "vector_store",
        ],
    }


@router.post("/reset-full", dependencies=admin_only)
def full_reset(db: Session = Depends(get_db)):
    """Nuclear reset - clears everything including setup. User must re-run wizard."""
    db.execute(text("DELETE FROM alerts"))
    db.execute(text("DELETE FROM alert_channels"))
    db.execute(text("DELETE FROM cost_events"))
    db.execute(text("DELETE FROM daily_cost_rollups"))
    db.execute(text("DELETE FROM budgets"))
    db.execute(text("DELETE FROM agents"))
    db.execute(text("DELETE FROM model_pricing"))
    db.execute(text("DELETE FROM providers"))
    db.execute(text("DELETE FROM setup_config"))
    db.commit()
    # Seeding only runs at startup; without re-seeding here, every event
    # ingested after a full reset (until the next restart) is priced at $0.
    from app.seed import seed_providers

    seed_providers(db)

    # Clear vector store so RAG doesn't answer from stale embeddings
    try:
        from app.services.vector_store import clear_vector_store

        clear_vector_store()
    except Exception:
        logger.warning("Failed to clear vector store during full reset", exc_info=True)

    return {
        "message": "Full reset complete. Default provider pricing restored; "
        "re-run the setup wizard."
    }


@router.get("/export", dependencies=admin_only)
def export_summary(db: Session = Depends(get_db)):
    """Export a summary of all data as JSON for backup."""
    events = db.query(CostEvent).order_by(CostEvent.timestamp.desc()).limit(1000).all()
    agents = db.query(Agent).all()

    return {
        "exported_at": utcnow().isoformat(),
        "agents": [
            {
                "name": a.name,
                "environment": a.environment,
                "swarm": a.swarm,
                "workflow": a.workflow,
                "is_active": a.is_active,
            }
            for a in agents
        ],
        "recent_events": [
            {
                "agent_name": e.agent_name,
                "model_name": e.model_name,
                "provider_name": e.provider_name,
                "tokens_input": e.tokens_input,
                "tokens_output": e.tokens_output,
                "tokens_cache_read": e.tokens_cache_read,
                "tokens_cache_write": e.tokens_cache_write,
                "tokens_cache_write_1h": e.tokens_cache_write_1h,
                "cost": e.cost,
                "status": e.status,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            }
            for e in events
        ],
        "total_events_in_db": db.query(func.count(CostEvent.id)).scalar() or 0,
    }


class RepriceRequest(BaseModel):
    from_date: datetime | None = None
    to_date: datetime | None = None
    dry_run: bool = False

    @field_validator("from_date", "to_date")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return to_naive_utc(v)

    @model_validator(mode="after")
    def _ordered(self):
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from_date must not be after to_date")
        return self


@router.post("/reprice", dependencies=admin_only)
def reprice(
    background_tasks: BackgroundTasks,
    body: RepriceRequest | None = None,
    db: Session = Depends(get_db),
):
    """Recompute cost for events stored without a price (cost_source
    'unpriced', or legacy rows at $0 with tokens) using current pricing, and
    recompute the daily rollups of the affected completed days.

    Use after adding/fixing model pricing. ``dry_run`` reports what would
    change without writing.
    """
    body = body or RepriceRequest()
    result = reprice_events(db, body.from_date, body.to_date, body.dry_run)
    ids = result.pop("repriced_ids")
    if ids and not body.dry_run:
        # Embedded event text includes the cost — refresh the cache.
        def _reembed(event_ids: list[int]) -> None:
            from app.database import SessionLocal
            from app.services.vector_store import embed_and_upsert_events

            bg_db = SessionLocal()
            try:
                embed_and_upsert_events(bg_db, event_ids)
            except Exception:
                logger.warning("Re-embedding repriced events failed", exc_info=True)
            finally:
                bg_db.close()

        background_tasks.add_task(_reembed, ids)
    logger.info(
        "Reprice: %d events, +%.6f cost (dry_run=%s)",
        result["repriced"],
        result["added_cost"],
        body.dry_run,
    )
    return result
