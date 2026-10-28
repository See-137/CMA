"""Data retention service — prunes old events without losing their spend.

For each day older than the retention cutoff that still has raw events:

- If the day has never been pruned, its rollup is recomputed from the complete
  set of events right before deletion (late or repriced events included), and
  the rollup rows are marked ``archived``.
- If the day is already archived (an earlier prune deleted its events), the
  surviving events arrived later; their aggregates are ADDED to the archived
  rows, which are never recomputed from the partial remainder.

Then the day's events and their embeddings are deleted.
"""

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.models import CostEvent, DailyCostRollup, SetupConfig
from app.services.rollup import compute_daily_rollups
from app.timeutils import utcnow

logger = logging.getLogger(__name__)


def _day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, datetime.min.time())
    return start, start + timedelta(days=1)


def _is_archived(db: Session, d: date) -> bool:
    return (
        db.query(DailyCostRollup.id)
        .filter(DailyCostRollup.date == d, DailyCostRollup.archived.is_(True))
        .first()
        is not None
    )


def _add_to_archived(db: Session, d: date, start: datetime, end: datetime) -> None:
    """Add the day's surviving (late) events to its archived rollup rows."""
    rows = (
        db.query(
            CostEvent.agent_name,
            CostEvent.model_name,
            CostEvent.provider_name,
            func.sum(CostEvent.cost).label("cost"),
            func.sum(CostEvent.tokens_input).label("tin"),
            func.sum(CostEvent.tokens_output).label("tout"),
            func.count(CostEvent.id).label("n"),
            func.sum(case((CostEvent.status == "failure", 1), else_=0)).label("fail"),
            func.avg(CostEvent.duration_ms).label("avg_ms"),
        )
        .filter(CostEvent.timestamp >= start, CostEvent.timestamp < end)
        .group_by(CostEvent.agent_name, CostEvent.model_name, CostEvent.provider_name)
        .all()
    )
    for r in rows:
        roll = (
            db.query(DailyCostRollup)
            .filter(
                DailyCostRollup.date == d,
                DailyCostRollup.agent_name == r.agent_name,
                DailyCostRollup.model_name == r.model_name,
                DailyCostRollup.provider_name == r.provider_name,
            )
            .first()
        )
        n = int(r.n or 0)
        if roll is None:
            db.add(
                DailyCostRollup(
                    date=d,
                    agent_name=r.agent_name,
                    model_name=r.model_name,
                    provider_name=r.provider_name,
                    total_cost=float(r.cost or 0),
                    total_tokens_input=int(r.tin or 0),
                    total_tokens_output=int(r.tout or 0),
                    request_count=n,
                    failure_count=int(r.fail or 0),
                    avg_duration_ms=float(r.avg_ms) if r.avg_ms is not None else None,
                    archived=True,
                )
            )
            continue
        old_n = roll.request_count or 0
        roll.total_cost = (roll.total_cost or 0) + float(r.cost or 0)
        roll.total_tokens_input = (roll.total_tokens_input or 0) + int(r.tin or 0)
        roll.total_tokens_output = (roll.total_tokens_output or 0) + int(r.tout or 0)
        roll.failure_count = (roll.failure_count or 0) + int(r.fail or 0)
        if r.avg_ms is not None:
            # Request-weighted; exact when every event carries a duration.
            prev = roll.avg_duration_ms if roll.avg_duration_ms is not None else 0.0
            roll.avg_duration_ms = (prev * old_n + float(r.avg_ms) * n) / (old_n + n)
        roll.request_count = old_n + n


def prune_old_events(db: Session) -> int:
    """Delete events older than the configured retention period, preserving
    their spend in (archived) daily rollups. Returns number of deleted events."""
    config = db.query(SetupConfig).first()
    if not config:
        return 0

    retention_days = config.data_retention_days or 90
    cutoff_date = (utcnow() - timedelta(days=retention_days)).date()

    earliest = (
        db.query(func.min(CostEvent.timestamp))
        .filter(
            CostEvent.timestamp < datetime.combine(cutoff_date, datetime.min.time())
        )
        .scalar()
    )
    if earliest is None:
        return 0

    deleted = 0
    pruned_ids: list[int] = []
    d = earliest.date()
    while d < cutoff_date:
        start, end = _day_bounds(d)
        ids = [
            row.id
            for row in db.query(CostEvent.id).filter(
                CostEvent.timestamp >= start, CostEvent.timestamp < end
            )
        ]
        if ids:
            if _is_archived(db, d):
                _add_to_archived(db, d, start, end)
            else:
                compute_daily_rollups(db, d)
                db.query(DailyCostRollup).filter(DailyCostRollup.date == d).update(
                    {"archived": True}, synchronize_session=False
                )
            deleted += (
                db.query(CostEvent)
                .filter(CostEvent.timestamp >= start, CostEvent.timestamp < end)
                .delete(synchronize_session=False)
            )
            # One transaction per day: rollup change and deletion commit together.
            db.commit()
            pruned_ids.extend(ids)
        d += timedelta(days=1)

    if deleted:
        # Drop the corresponding embeddings so the vector cache doesn't keep
        # citing events that no longer exist in the source-of-truth DB.
        try:
            from app.services.vector_store import delete_events

            delete_events(pruned_ids)
        except Exception:
            logger.warning("Failed to prune event embeddings", exc_info=True)
        logger.info("Retention: pruned %d events older than %s", deleted, cutoff_date)

    return deleted
