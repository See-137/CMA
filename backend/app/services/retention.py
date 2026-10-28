"""Data retention service — prunes old events without losing their spend.

For each day older than the retention cutoff that still has raw events:

- If the day has never been pruned, its rollup rows are rewritten from the
  complete set of events being deleted (late or repriced events included), and
  marked ``archived``.
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


_CHUNK = 1000  # bound IN-lists (SQLite's variable limit, PG plan size)


def _aggregate_ids(db: Session, ids: list[int]) -> dict[tuple, dict]:
    """Per (agent, model, provider) aggregates over exactly these event ids."""
    groups: dict[tuple, dict] = {}
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i : i + _CHUNK]
        rows = (
            db.query(
                CostEvent.agent_name,
                CostEvent.model_name,
                CostEvent.provider_name,
                func.sum(CostEvent.cost).label("cost"),
                func.sum(CostEvent.tokens_input).label("tin"),
                func.sum(CostEvent.tokens_output).label("tout"),
                func.count(CostEvent.id).label("n"),
                func.sum(case((CostEvent.status == "failure", 1), else_=0)).label(
                    "fail"
                ),
                func.sum(CostEvent.duration_ms).label("dur_sum"),
                func.count(CostEvent.duration_ms).label("dur_n"),
            )
            .filter(CostEvent.id.in_(chunk))
            .group_by(
                CostEvent.agent_name, CostEvent.model_name, CostEvent.provider_name
            )
            .all()
        )
        for r in rows:
            g = groups.setdefault(
                (r.agent_name, r.model_name, r.provider_name),
                {
                    "cost": 0.0,
                    "tin": 0,
                    "tout": 0,
                    "n": 0,
                    "fail": 0,
                    "dur_sum": 0.0,
                    "dur_n": 0,
                },
            )
            g["cost"] += float(r.cost or 0)
            g["tin"] += int(r.tin or 0)
            g["tout"] += int(r.tout or 0)
            g["n"] += int(r.n or 0)
            g["fail"] += int(r.fail or 0)
            g["dur_sum"] += float(r.dur_sum or 0)
            g["dur_n"] += int(r.dur_n or 0)
    return groups


def _fold_into_rollups(db: Session, d: date, ids: list[int], replace: bool) -> None:
    """Write exactly the events in ``ids`` into day ``d``'s rollup rows, then
    mark the day archived. ``replace`` (never-pruned day: ids are ALL of the
    day's events) sets each group's totals; otherwise (already archived: ids
    are late arrivals) adds to them. No commit — the caller commits together
    with the deletion of the same ids."""
    groups = _aggregate_ids(db, ids)
    existing = {
        (r.agent_name, r.model_name, r.provider_name): r
        for r in db.query(DailyCostRollup).filter(DailyCostRollup.date == d)
    }
    if replace:
        for key, row in existing.items():
            if key not in groups:
                db.delete(row)  # stale group: its events no longer exist
    for key, g in groups.items():
        roll = existing.get(key)
        if roll is None or replace:
            if roll is None:
                roll = DailyCostRollup(
                    date=d, agent_name=key[0], model_name=key[1], provider_name=key[2]
                )
                db.add(roll)
            roll.total_cost = g["cost"]
            roll.total_tokens_input = g["tin"]
            roll.total_tokens_output = g["tout"]
            roll.request_count = g["n"]
            roll.failure_count = g["fail"]
            roll.avg_duration_ms = g["dur_sum"] / g["dur_n"] if g["dur_n"] else None
        else:
            old_n = roll.request_count or 0
            roll.total_cost = (roll.total_cost or 0) + g["cost"]
            roll.total_tokens_input = (roll.total_tokens_input or 0) + g["tin"]
            roll.total_tokens_output = (roll.total_tokens_output or 0) + g["tout"]
            roll.failure_count = (roll.failure_count or 0) + g["fail"]
            if g["dur_n"]:
                if roll.avg_duration_ms is None:
                    # The archived row had no durations: don't dilute by its
                    # request count.
                    roll.avg_duration_ms = g["dur_sum"] / g["dur_n"]
                else:
                    # Request-weighted; exact when every event carries a duration.
                    roll.avg_duration_ms = (
                        roll.avg_duration_ms * old_n + g["dur_sum"]
                    ) / (old_n + g["dur_n"])
            roll.request_count = old_n + g["n"]
    db.flush()
    db.query(DailyCostRollup).filter(DailyCostRollup.date == d).update(
        {"archived": True}, synchronize_session=False
    )


def prune_old_events(db: Session) -> int:
    """Delete events older than the configured retention period, preserving
    their spend in (archived) daily rollups. Returns number of deleted events.

    Per day, the rollup is written from exactly the event ids that are then
    deleted, in one transaction: an event inserted concurrently (late or
    back-dated) is neither counted nor deleted, and is folded in next run.
    """
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
            _fold_into_rollups(db, d, ids, replace=not _is_archived(db, d))
            for i in range(0, len(ids), _CHUNK):
                deleted += (
                    db.query(CostEvent)
                    .filter(CostEvent.id.in_(ids[i : i + _CHUNK]))
                    .delete(synchronize_session=False)
                )
            db.commit()  # rollup write + deletion of the same ids, atomically
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
