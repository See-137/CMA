"""Data retention service — prunes old events after rollup."""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.models import CostEvent, DailyCostRollup, SetupConfig

logger = logging.getLogger(__name__)


def prune_old_events(db: Session) -> int:
    """Delete events older than the configured retention period.

    Only deletes events for dates that have been rolled up (safety check).
    Returns number of deleted events.
    """
    config = db.query(SetupConfig).first()
    if not config:
        return 0

    retention_days = config.data_retention_days or 90
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    cutoff_date = cutoff.date()

    # Safety: only delete events for dates that have rollups
    rolled_up_dates = (
        db.query(DailyCostRollup.date)
        .filter(DailyCostRollup.date < cutoff_date)
        .distinct()
        .all()
    )

    if not rolled_up_dates:
        return 0

    dates = [r.date for r in rolled_up_dates]

    deleted = 0
    pruned_ids: list[int] = []
    for d in dates:
        start = datetime.combine(d, datetime.min.time())
        end = start + timedelta(days=1)
        ids = [
            row.id
            for row in db.query(CostEvent.id).filter(
                CostEvent.timestamp >= start, CostEvent.timestamp < end
            )
        ]
        if not ids:
            continue
        pruned_ids.extend(ids)
        deleted += (
            db.query(CostEvent)
            .filter(CostEvent.timestamp >= start, CostEvent.timestamp < end)
            .delete(synchronize_session=False)
        )

    if deleted:
        db.commit()
        # Drop the corresponding embeddings so the vector cache doesn't keep
        # citing events that no longer exist in the source-of-truth DB.
        try:
            from app.services.vector_store import delete_events

            delete_events(pruned_ids)
        except Exception:
            logger.warning("Failed to prune event embeddings", exc_info=True)
        logger.info("Retention: pruned %d events older than %s", deleted, cutoff_date)

    return deleted
