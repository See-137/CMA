"""Retention must never remove spend from the totals."""

from datetime import timedelta

from app.models.models import CostEvent, DailyCostRollup
from app.services.retention import prune_old_events
from app.services.rollup import compute_daily_rollups
from app.timeutils import utcnow
from tests.conftest import TestSession


def _event(ts, cost):
    return CostEvent(
        agent_name="old-bot",
        model_name="gpt-4o",
        provider_name="OpenAI",
        tokens_input=1,
        tokens_output=1,
        cost=cost,
        timestamp=ts,
    )


def test_late_event_is_in_rollup_before_prune(authed_client):
    db = TestSession()
    try:
        old = (utcnow() - timedelta(days=200)).replace(hour=12, microsecond=0)
        db.add(_event(old, 1.0))
        db.commit()
        compute_daily_rollups(db, old.date())
        # A back-dated event arrives after that day was rolled up.
        db.add(_event(old + timedelta(hours=1), 2.0))
        db.commit()

        assert prune_old_events(db) == 2
        roll = db.query(DailyCostRollup).filter_by(date=old.date()).one()
        assert roll.total_cost == 3.0
        assert roll.request_count == 2
        assert db.query(CostEvent).count() == 0
    finally:
        db.close()


def test_backdated_event_after_prune_adds_to_archived_rollup(authed_client):
    """A day already pruned has only its rollup left; a late event for that
    day must be ADDED to the archived totals, never replace them."""
    db = TestSession()
    try:
        old = (utcnow() - timedelta(days=200)).replace(hour=12, microsecond=0)
        for _ in range(50):
            db.add(_event(old, 2.0))
        db.commit()
        assert prune_old_events(db) == 50  # rollup computed, events gone
        roll = db.query(DailyCostRollup).filter_by(date=old.date()).one()
        assert roll.total_cost == 100.0

        db.add(_event(old + timedelta(hours=2), 2.0))  # back-dated arrival
        db.commit()
        # Nothing may recompute the archived day from the lone survivor.
        compute_daily_rollups(db, old.date())
        db.refresh(roll)
        assert roll.total_cost == 100.0

        assert prune_old_events(db) == 1
        db.refresh(roll)
        assert roll.total_cost == 102.0
        assert roll.request_count == 51
    finally:
        db.close()


def test_old_day_without_rollup_is_rolled_up_then_pruned(authed_client):
    db = TestSession()
    try:
        old = (utcnow() - timedelta(days=200)).replace(hour=12, microsecond=0)
        db.add(_event(old, 3.0))
        db.commit()
        assert prune_old_events(db) == 1
        roll = db.query(DailyCostRollup).filter_by(date=old.date()).one()
        assert roll.total_cost == 3.0
    finally:
        db.close()


def test_event_arriving_mid_prune_is_neither_lost_nor_double_counted(
    authed_client, monkeypatch
):
    """Rollup and deletion use the same id set: an event inserted for the day
    after it was aggregated must survive this run and be folded in next run
    (independent review M1: it used to be deleted uncounted)."""
    from app.services import retention

    db = TestSession()
    try:
        old = (utcnow() - timedelta(days=200)).replace(hour=12, microsecond=0)
        db.add(_event(old, 1.0))
        db.commit()

        real_fold = retention._fold_into_rollups

        def fold_then_concurrent_insert(session, d, ids, replace):
            real_fold(session, d, ids, replace)
            session.add(_event(old + timedelta(minutes=5), 5.0))  # late arrival
            session.flush()

        monkeypatch.setattr(
            retention, "_fold_into_rollups", fold_then_concurrent_insert
        )
        assert prune_old_events(db) == 1
        monkeypatch.setattr(retention, "_fold_into_rollups", real_fold)

        assert db.query(CostEvent).count() == 1  # the late event survived
        roll = db.query(DailyCostRollup).filter_by(date=old.date()).one()
        assert roll.total_cost == 1.0

        assert prune_old_events(db) == 1
        db.refresh(roll)
        assert roll.total_cost == 6.0 and roll.request_count == 2
    finally:
        db.close()


def test_late_duration_does_not_get_diluted_by_durationless_archive(authed_client):
    db = TestSession()
    try:
        old = (utcnow() - timedelta(days=200)).replace(hour=12, microsecond=0)
        for _ in range(50):
            db.add(_event(old, 1.0))  # no duration_ms
        db.commit()
        prune_old_events(db)
        late = _event(old + timedelta(hours=1), 1.0)
        late.duration_ms = 500
        db.add(late)
        db.commit()
        prune_old_events(db)
        roll = db.query(DailyCostRollup).filter_by(date=old.date()).one()
        assert roll.avg_duration_ms == 500
    finally:
        db.close()
