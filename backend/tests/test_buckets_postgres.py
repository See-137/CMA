"""SQL bucketing on PostgreSQL must match the SQLite path exactly (D2).

The regular suite runs on SQLite only; this runs when CMA_TEST_PG_URL points
at an empty, disposable Postgres database (CI's backend-test job sets it).
"""

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session

from app.database import Base
from app.models.models import CostEvent
from app.services.buckets import bucket_start_utc, local_bucket_expr
from app.timeutils import ZoneInfo, utc_iso

PG_URL = os.environ.get("CMA_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="CMA_TEST_PG_URL not set")

ZONES = [
    "UTC",
    "Asia/Jerusalem",
    "America/New_York",
    "America/Havana",
    "Africa/Cairo",
    "America/Santiago",
    "Asia/Kolkata",
    "Asia/Kathmandu",
    "Australia/Lord_Howe",
]


def _stamps():
    """Every 10 minutes across this year's DST transitions."""
    out = []
    for day in ("2026-03-27", "2026-04-24", "2026-09-06", "2026-10-24", "2026-11-01"):
        start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) - timedelta(
            hours=12
        )
        out += [
            (start + timedelta(minutes=10 * i)).replace(tzinfo=None)
            for i in range(6 * 48)
        ]
    return out


def _buckets(engine):
    with Session(engine) as db:
        db.add_all(
            CostEvent(
                agent_name="a",
                model_name="m",
                provider_name="p",
                tokens_input=0,
                tokens_output=0,
                cost=1.0,
                timestamp=ts,
            )
            for ts in _stamps()
        )
        db.commit()
        out = {}
        for name in ZONES:
            tz = ZoneInfo(name)
            for unit in ("hour", "day"):
                expr = local_bucket_expr(CostEvent.timestamp, unit, tz, db).label("b")
                rows = db.query(expr, func.count()).group_by("b").order_by("b").all()
                out[(name, unit)] = [
                    (utc_iso(bucket_start_utc(b, unit, tz)), n) for b, n in rows
                ]
        return out


def test_postgres_buckets_match_sqlite():
    sqlite = create_engine("sqlite://")
    pg = create_engine(PG_URL)
    Base.metadata.create_all(sqlite)
    Base.metadata.create_all(pg)
    try:
        expected = _buckets(sqlite)
        actual = _buckets(pg)
    finally:
        Base.metadata.drop_all(pg)
        pg.dispose()
    assert actual == expected
    # Absolute anchors on the Postgres result (the comparison alone would pass
    # if both paths were wrong the same way):
    hours = dict(actual[("America/New_York", "hour")])
    # fall-back hours stay separate: 01:00 EDT (05:00Z) and 01:00 EST (06:00Z)
    assert hours["2026-11-01T05:00Z"] == hours["2026-11-01T06:00Z"] == 6
    days = dict(actual[("Asia/Jerusalem", "day")])
    # 2026-10-24 local (IDT, +3) = 23:00Z..21:00Z: a full 24 h of 10-min events
    assert days["2026-10-23T21:00Z"] == 144
    days = dict(actual[("Africa/Cairo", "day")])
    # 2026-04-24 has no 00:00 (DST at midnight): the day starts 22:00Z
    assert "2026-04-23T22:00Z" in days
