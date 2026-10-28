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
