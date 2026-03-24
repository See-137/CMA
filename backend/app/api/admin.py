import os
import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.models import CostEvent, Agent, Budget, Alert, SetupConfig

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)


@router.get("/status")
def system_status(db: Session = Depends(get_db)):
    """System health and stats."""
    db_path = os.path.join("data", "cma.db")
    db_size_mb = (
        round(os.path.getsize(db_path) / (1024 * 1024), 2)
        if os.path.exists(db_path)
        else 0
    )

    config = db.query(SetupConfig).first()

    return {
        "status": "running",
        "version": "0.1.0",
        "deployment_name": config.deployment_name if config else "CMA",
        "uptime_check": datetime.utcnow().isoformat(),
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


@router.get("/logs")
def recent_logs():
    """Return recent application log lines."""
    log_file = os.path.join("data", "cma.log")
    if not os.path.exists(log_file):
        return {"lines": ["No log file found. Logs are printed to stdout."]}

    with open(log_file, "r") as f:
        lines = f.readlines()

    # Return last 100 lines
    return {"lines": [line.rstrip() for line in lines[-100:]]}


@router.post("/reset")
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


@router.post("/reset-full")
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

    # Clear vector store so RAG doesn't answer from stale embeddings
    try:
        from app.services.vector_store import clear_vector_store

        clear_vector_store()
    except Exception:
        logger.warning("Failed to clear vector store during full reset", exc_info=True)

    return {
        "message": "Full reset complete. Please restart the application to re-run setup."
    }


@router.get("/export")
def export_summary(db: Session = Depends(get_db)):
    """Export a summary of all data as JSON for backup."""
    events = db.query(CostEvent).order_by(CostEvent.timestamp.desc()).limit(1000).all()
    agents = db.query(Agent).all()

    return {
        "exported_at": datetime.utcnow().isoformat(),
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
                "cost": e.cost,
                "status": e.status,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            }
            for e in events
        ],
        "total_events_in_db": db.query(func.count(CostEvent.id)).scalar() or 0,
    }
