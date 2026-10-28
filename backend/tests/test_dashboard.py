import pytest


def test_dashboard_overview(authed_client):
    resp = authed_client.get("/api/v1/dashboard/overview", params={"period": "today"})
    assert resp.status_code == 200
    data = resp.json()
    assert "total_cost" in data
    assert "total_requests" in data
    assert "active_agents" in data


def test_dashboard_with_data(authed_client):
    authed_client.post(
        "/api/v1/events",
        json={
            "agent_name": "dash-bot",
            "model": "gpt-4o",
            "provider": "OpenAI",
            "tokens_input": 1000,
            "tokens_output": 500,
            "cost": 0.05,
        },
    )
    resp = authed_client.get("/api/v1/dashboard/overview", params={"period": "today"})
    data = resp.json()
    assert data["total_cost"] > 0
    assert data["total_requests"] == 1


def test_dashboard_timeseries(authed_client):
    resp = authed_client.get(
        "/api/v1/dashboard/timeseries", params={"period": "week", "granularity": "day"}
    )
    assert resp.status_code == 200
    assert "data" in resp.json()


def test_dashboard_top_agents(authed_client):
    authed_client.post(
        "/api/v1/events",
        json={
            "agent_name": "top-bot",
            "model": "gpt-4o",
            "provider": "OpenAI",
            "tokens_input": 1000,
            "tokens_output": 500,
            "cost": 0.10,
        },
    )
    resp = authed_client.get("/api/v1/dashboard/top-agents", params={"period": "today"})
    assert resp.status_code == 200
    agents = resp.json()["data"]
    assert len(agents) >= 1


def test_dashboard_budget_status(authed_client):
    authed_client.post(
        "/api/v1/budgets",
        json={
            "name": "Dash Budget",
            "scope": "global",
            "period": "daily",
            "limit_amount": 100.0,
        },
    )
    resp = authed_client.get("/api/v1/dashboard/budget-status")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) >= 1


def test_api_datetimes_are_explicit_utc(authed_client):
    """Naive ISO strings are parsed as *local* time by browsers; every API
    datetime must carry an explicit UTC designator."""
    authed_client.post(
        "/api/v1/events",
        json={
            "agent_name": "z-bot",
            "model": "gpt-4o",
            "provider": "OpenAI",
            "tokens_input": 1,
            "tokens_output": 1,
        },
    )
    item = authed_client.get("/api/v1/events").json()["items"][0]
    assert item["timestamp"].endswith("Z")
    assert item["created_at"].endswith("Z")
    agent = authed_client.get("/api/v1/agents").json()[0]
    assert agent["created_at"].endswith("Z")
    for granularity in ("day", "hour"):
        points = authed_client.get(
            "/api/v1/dashboard/timeseries",
            params={"period": "today", "granularity": granularity},
        ).json()["data"]
        assert points and all(p["timestamp"].endswith("Z") for p in points)


@pytest.mark.parametrize(
    "period,now,expected",
    [
        # 09:30 today -> yesterday 00:00..09:30 (not all of yesterday)
        ("today", (2026, 10, 28, 9, 30), ((2026, 10, 27), (2026, 10, 27, 9, 30))),
        # Wed 12:00 -> previous Mon 00:00..Wed 12:00
        ("week", (2026, 10, 28, 12, 0), ((2026, 10, 19), (2026, 10, 21, 12, 0))),
        # Mar 31 -> Feb 1.. clamped at Mar 1 (Feb is shorter)
        ("month", (2026, 3, 31, 12, 0), ((2026, 2, 1), (2026, 3, 1))),
        # Jan -> previous year's December
        ("month", (2026, 1, 15), ((2025, 12, 1), (2025, 12, 15))),
        # quarter used to compare against yesterday
        ("quarter", (2026, 11, 2), ((2026, 7, 1), (2026, 8, 2))),
    ],
)
def test_prev_period_is_like_for_like(monkeypatch, period, now, expected):
    from datetime import datetime, timezone

    from app.api import dashboard

    def naive_utc(*parts):
        return datetime(*parts, tzinfo=timezone.utc).replace(tzinfo=None)

    monkeypatch.setattr(dashboard, "utcnow", lambda: naive_utc(*now))
    from app.timeutils import UTC_ZONE

    start, end = dashboard._prev_period_bounds(period, UTC_ZONE)
    assert (start, end) == (naive_utc(*expected[0]), naive_utc(*expected[1]))
