"""Decision D2: budget periods, dashboard days and Scrooge's named periods
follow the deployment time zone; storage stays naive UTC."""

from datetime import datetime, timedelta, timezone

import pytest

from app import timeutils
from app.api import dashboard
from app.models.models import Budget, CostEvent, SetupConfig
from app.services import rag_engine as rag
from app.timeutils import ZoneInfo, local_floor, shift_local
from tests.conftest import TestSession


def U(*parts):  # naive UTC, as stored
    return datetime(*parts, tzinfo=timezone.utc).replace(tzinfo=None)


TOKYO = "Asia/Tokyo"  # UTC+9, no DST
# 2026-10-28 02:00 UTC = 11:00 in Tokyo; Tokyo's "today" began 2026-10-27 15:00 UTC.
NOW = U(2026, 10, 28, 2, 0)


@pytest.fixture
def db(authed_client):
    s = TestSession()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def tokyo(db, monkeypatch):
    db.query(SetupConfig).update({"timezone": TOKYO})
    db.commit()
    for mod in (timeutils, dashboard, rag):
        monkeypatch.setattr(mod, "utcnow", lambda: NOW)
    monkeypatch.setattr(rag, "embed_text", lambda q: [0.0] * 384)
    monkeypatch.setattr(rag, "search_events", lambda *a, **k: [])
    monkeypatch.setattr(rag, "search_rollups", lambda *a, **k: [])
    return db


def _event(db, ts, cost, agent="a"):
    db.add(
        CostEvent(
            agent_name=agent,
            model_name="gpt-4o",
            provider_name="openai",
            tokens_input=1,
            tokens_output=1,
            cost=cost,
            timestamp=ts,
        )
    )
    db.commit()


# -- helpers -------------------------------------------------------------


def test_local_day_across_dst_end_is_25_hours():
    tz = ZoneInfo("Asia/Jerusalem")  # DST ends 2026-10-25 02:00 local
    day = local_floor("day", tz, U(2026, 10, 26, 12, 0))
    assert day == U(2026, 10, 25, 22, 0)  # local midnight, UTC+2
    prev = shift_local(day, tz, days=-1)
    assert prev == U(2026, 10, 24, 21, 0)  # local midnight, UTC+3
    assert day - prev == timedelta(hours=25)


def test_half_hour_zone_hours_and_month_start():
    tz = ZoneInfo("Asia/Kolkata")  # UTC+5:30
    assert local_floor("hour", tz, U(2026, 10, 28, 22, 45)) == U(2026, 10, 28, 22, 30)
    tz = ZoneInfo("Asia/Jerusalem")
    assert local_floor("month", tz, U(2026, 10, 28, 22, 30)) == U(2026, 9, 30, 21, 0)


def test_unknown_or_missing_zone_falls_back_to_utc(db):
    db.query(SetupConfig).update({"timezone": "Mars/Olympus"})
    db.commit()
    assert timeutils.deployment_tz(db).key == "UTC"
    db.query(SetupConfig).update({"timezone": None})
    db.commit()
    assert timeutils.deployment_tz(db).key == "UTC"


# -- setup ---------------------------------------------------------------


def test_setup_rejects_unknown_zone(client):
    resp = client.post(
        "/api/v1/setup",
        json={
            "admin_email": "a@b.c",
            "admin_password": "pw123456",
            "timezone": "Nowhere/X",
        },
    )
    assert resp.status_code == 422


def test_setup_stores_valid_zone(client):
    resp = client.post(
        "/api/v1/setup",
        json={"admin_email": "a@b.c", "admin_password": "pw123456", "timezone": TOKYO},
    )
    assert resp.status_code == 200
    assert client.get("/api/v1/setup/status").json()["timezone"] == TOKYO


# -- budgets -------------------------------------------------------------


def test_daily_budget_resets_at_local_midnight(tokyo, authed_client):
    _event(tokyo, U(2026, 10, 27, 14, 0), 1.0)  # 23:00 Tokyo, yesterday
    _event(tokyo, U(2026, 10, 27, 16, 0), 2.0)  # 01:00 Tokyo, today
    tokyo.add(
        Budget(
            name="day",
            scope="global",
            period="daily",
            limit_amount=2.0,
            control_action="block",
        )
    )
    tokyo.commit()
    (budget,) = authed_client.get("/api/v1/budgets").json()
    # UTC "today" (from 2026-10-28 00:00 UTC) would be $0; Tokyo's is $2.
    assert budget["current_spend"] == pytest.approx(2.0)
    check = authed_client.get("/api/v1/budgets/check").json()
    assert check["allowed"] is False


def test_threshold_alert_dedup_uses_local_period(tokyo, authed_client):
    from app.models.models import Alert
    from app.services.budget_checker import check_budgets

    tokyo.add(
        Budget(
            name="day",
            scope="global",
            period="daily",
            limit_amount=1.0,
            alert_thresholds="[100]",
        )
    )
    tokyo.commit()
    _event(tokyo, U(2026, 10, 27, 16, 0), 2.0)
    # An alert raised before Tokyo midnight belongs to the previous period.
    tokyo.add(
        Alert(
            budget_id=1,
            alert_type="budget_exceeded",
            severity="critical",
            message="old (threshold: 100%)",
            created_at=U(2026, 10, 27, 14, 0),
        )
    )
    tokyo.commit()
    assert len(check_budgets(tokyo)) == 1
    assert check_budgets(tokyo) == []


# -- dashboard -----------------------------------------------------------


def test_dashboard_today_and_day_buckets_are_local(tokyo, authed_client):
    _event(tokyo, U(2026, 10, 27, 14, 0), 1.0)  # Tokyo Oct 27
    _event(tokyo, U(2026, 10, 27, 16, 0), 2.0)  # Tokyo Oct 28
    overview = authed_client.get("/api/v1/dashboard/overview?period=today").json()
    assert overview["total_cost"] == pytest.approx(2.0)

    series = authed_client.get(
        "/api/v1/dashboard/timeseries?period=week&granularity=day"
    ).json()
    assert series["timezone"] == TOKYO
    assert [(p["timestamp"], p["cost"]) for p in series["data"]] == [
        ("2026-10-26T15:00Z", 1.0),
        ("2026-10-27T15:00Z", 2.0),
    ]


def test_hour_buckets_in_a_half_hour_zone(db, monkeypatch, authed_client):
    db.query(SetupConfig).update({"timezone": "Asia/Kolkata"})
    db.commit()
    monkeypatch.setattr(dashboard, "utcnow", lambda: NOW)
    _event(db, U(2026, 10, 27, 20, 10), 1.0)  # 01:40 IST -> 01:00 bucket
    _event(db, U(2026, 10, 27, 20, 40), 2.0)  # 02:10 IST -> 02:00 bucket
    series = authed_client.get(
        "/api/v1/dashboard/timeseries?period=today&granularity=hour"
    ).json()
    assert [p["timestamp"] for p in series["data"]] == [
        "2026-10-27T19:30Z",
        "2026-10-27T20:30Z",
    ]


# -- Scrooge -------------------------------------------------------------


def test_scrooge_today_and_yesterday_are_local(tokyo):
    intent = rag.detect_query_intent("what did we spend yesterday vs today", tokyo)
    assert intent.time_range == (
        U(2026, 10, 26, 15, 0),
        U(2026, 10, 27, 15, 0),
    )
    ((label, window),) = intent.extra_windows
    assert label == "today" and window == (U(2026, 10, 27, 15, 0), NOW)


def test_scrooge_weekday_is_local(tokyo):
    # Tokyo: Wednesday 2026-10-28. "monday" = Oct 26 local = Oct 25 15:00 UTC.
    intent = rag.detect_query_intent("how much did we spend on monday", tokyo)
    assert intent.time_range == (
        U(2026, 10, 25, 15, 0),
        U(2026, 10, 26, 15, 0),
    )


def test_scrooge_scope_states_the_zone_and_timeline_is_local(tokyo):
    _event(tokyo, U(2026, 10, 27, 14, 0), 1.0)
    _event(tokyo, U(2026, 10, 27, 16, 0), 2.0)
    ctx = rag.retrieve("show the cost trend this month", tokyo)
    assert any("Asia/Tokyo" in line for line in ctx.scope)
    timeline = [r for r in ctx.sql_results if r.get("type") == "timeline"]
    assert [(r["bucket"], r["cost"]) for r in timeline] == [
        ("2026-10-26T15:00Z", 1.0),
        ("2026-10-27T15:00Z", 2.0),
    ]
