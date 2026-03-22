"""Daily cost rollup aggregation service."""

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.models import CostEvent, DailyCostRollup

logger = logging.getLogger(__name__)


def compute_daily_rollups(db: Session, target_date: date | None = None) -> int:
    """Aggregate cost events for a given date into daily_cost_rollups.

    Uses upsert logic so it's idempotent — safe to re-run.
    Returns number of rows upserted.
    """
    if target_date is None:
        target_date = (datetime.utcnow() - timedelta(days=1)).date()

    start = datetime.combine(target_date, datetime.min.time())
    end = start + timedelta(days=1)

    failure_expr = func.sum(case((CostEvent.status == "failure", 1), else_=0))

    rows = (
        db.query(
            CostEvent.agent_name,
            CostEvent.model_name,
            CostEvent.provider_name,
            func.sum(CostEvent.cost).label("total_cost"),
            func.sum(CostEvent.tokens_input).label("total_tokens_input"),
            func.sum(CostEvent.tokens_output).label("total_tokens_output"),
            func.count(CostEvent.id).label("request_count"),
            failure_expr.label("failure_count"),
            func.avg(CostEvent.duration_ms).label("avg_duration_ms"),
        )
        .filter(CostEvent.timestamp >= start, CostEvent.timestamp < end)
        .group_by(CostEvent.agent_name, CostEvent.model_name, CostEvent.provider_name)
        .all()
    )

    if not rows:
        logger.info("No events found for %s, skipping rollup", target_date)
        return 0

    count = 0
    for row in rows:
        existing = (
            db.query(DailyCostRollup)
            .filter(
                DailyCostRollup.date == target_date,
                DailyCostRollup.agent_name == row.agent_name,
                DailyCostRollup.model_name == row.model_name,
                DailyCostRollup.provider_name == row.provider_name,
            )
            .first()
        )

        values = dict(
            total_cost=float(row.total_cost or 0),
            total_tokens_input=int(row.total_tokens_input or 0),
            total_tokens_output=int(row.total_tokens_output or 0),
            request_count=int(row.request_count or 0),
            failure_count=int(row.failure_count or 0),
            avg_duration_ms=float(row.avg_duration_ms) if row.avg_duration_ms else None,
        )

        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            rollup = DailyCostRollup(
                date=target_date,
                agent_name=row.agent_name,
                model_name=row.model_name,
                provider_name=row.provider_name,
                **values,
            )
            db.add(rollup)
        count += 1

    db.commit()
    logger.info("Rollup complete for %s: %d groups", target_date, count)
    return count


def backfill_rollups(db: Session) -> int:
    """Compute rollups for any dates that are missing."""
    earliest = db.query(func.min(CostEvent.timestamp)).scalar()
    if not earliest:
        return 0

    total = 0
    current = earliest.date()
    yesterday = (datetime.utcnow() - timedelta(days=1)).date()

    while current <= yesterday:
        has_rollup = (
            db.query(DailyCostRollup).filter(DailyCostRollup.date == current).first()
        )
        if not has_rollup:
            total += compute_daily_rollups(db, current)
        current += timedelta(days=1)

    if total:
        logger.info("Backfill complete: %d rollup groups created", total)
    return total
