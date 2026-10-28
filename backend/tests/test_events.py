import pytest


def test_ingest_single_event(authed_client):
    resp = authed_client.post(
        "/api/v1/events",
        json={
            "agent_name": "test-bot",
            "model": "gpt-4o",
            "provider": "OpenAI",
            "tokens_input": 1000,
            "tokens_output": 500,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["received"] == 1
    assert data["processed"] == 1


def test_ingest_batch_events(authed_client):
    resp = authed_client.post(
        "/api/v1/events",
        json=[
            {
                "agent_name": "bot-1",
                "model": "gpt-4o",
                "provider": "OpenAI",
                "tokens_input": 100,
                "tokens_output": 50,
            },
            {
                "agent_name": "bot-2",
                "model": "claude-3.5-sonnet",
                "provider": "Anthropic",
                "tokens_input": 200,
                "tokens_output": 100,
            },
        ],
    )
    assert resp.status_code == 200
    assert resp.json()["processed"] == 2


def test_auto_discover_agent(authed_client):
    authed_client.post(
        "/api/v1/events",
        json={
            "agent_name": "new-agent",
            "model": "gpt-4o",
            "provider": "OpenAI",
            "tokens_input": 100,
            "tokens_output": 50,
        },
    )
    resp = authed_client.get("/api/v1/agents")
    agents = resp.json()
    assert any(a["name"] == "new-agent" for a in agents)


def test_cost_calculation(authed_client):
    authed_client.post(
        "/api/v1/events",
        json={
            "agent_name": "calc-bot",
            "model": "gpt-4o",
            "provider": "OpenAI",
            "tokens_input": 1000000,  # 1M tokens
            "tokens_output": 0,
        },
    )
    resp = authed_client.get("/api/v1/events")
    events = resp.json()["items"]
    # gpt-4o input price is $2.50/M tokens
    assert events[0]["cost"] == pytest.approx(2.5, abs=0.1)


def test_list_events_with_filters(authed_client):
    authed_client.post(
        "/api/v1/events",
        json=[
            {
                "agent_name": "a1",
                "model": "gpt-4o",
                "provider": "OpenAI",
                "tokens_input": 100,
                "tokens_output": 50,
                "status": "success",
            },
            {
                "agent_name": "a2",
                "model": "gpt-4o",
                "provider": "OpenAI",
                "tokens_input": 100,
                "tokens_output": 50,
                "status": "failure",
            },
        ],
    )
    resp = authed_client.get("/api/v1/events", params={"status": "failure"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["agent_name"] == "a2"


def test_tz_aware_timestamp_is_stored_as_utc(authed_client):
    """An offset timestamp must be converted to UTC, not stored as wall-clock.

    Regression: SQLite dropped the offset and PostgreSQL converted to the
    server's session time zone, mis-dating events (and their budget period).
    """
    resp = authed_client.post(
        "/api/v1/events",
        json={
            "agent_name": "tz-bot",
            "model": "gpt-4o",
            "provider": "OpenAI",
            "tokens_input": 1,
            "tokens_output": 1,
            "timestamp": "2026-10-28T23:30:00-05:00",
        },
    )
    assert resp.json()["processed"] == 1
    items = authed_client.get("/api/v1/events").json()["items"]
    assert items[0]["timestamp"].startswith("2026-10-29T04:30:00")


def test_event_list_date_filter_accepts_offsets(authed_client):
    authed_client.post(
        "/api/v1/events",
        json={
            "agent_name": "tz-bot",
            "model": "gpt-4o",
            "provider": "OpenAI",
            "tokens_input": 1,
            "tokens_output": 1,
            "timestamp": "2026-10-29T04:30:00Z",
        },
    )
    # 06:00+02:00 == 04:00Z, so the event (04:30Z) is after the bound.
    resp = authed_client.get(
        "/api/v1/events",
        params={"from_date": "2026-10-29T06:00:00+02:00"},
    )
    assert resp.json()["total"] == 1
    resp = authed_client.get(
        "/api/v1/events",
        params={"from_date": "2026-10-29T07:00:00+02:00"},
    )
    assert resp.json()["total"] == 0


def test_agent_autodiscovery_race_does_not_drop_event(authed_client, monkeypatch):
    """Two requests seeing "no agent" at once: the loser must reuse the row.

    Simulated deterministically: the agent already exists (another request
    won), but this request's lookup ran before that commit and saw nothing.
    Previously the UNIQUE violation failed the event's savepoint and the
    event was silently dropped while the request still returned 200.
    """
    from sqlalchemy.orm import Query

    from app.models.models import Agent

    authed_client.post(
        "/api/v1/events",
        json={
            "agent_name": "racer",
            "model": "gpt-4o",
            "provider": "OpenAI",
            "tokens_input": 1,
            "tokens_output": 1,
        },
    )

    real_first = Query.first
    calls = {"n": 0}

    def stale_first(self):
        # Only the auto-discovery lookup selects Agent by itself.
        if self.column_descriptions[0]["entity"] is Agent and calls["n"] == 0:
            calls["n"] += 1
            return None
        return real_first(self)

    monkeypatch.setattr(Query, "first", stale_first)
    resp = authed_client.post(
        "/api/v1/events",
        json={
            "agent_name": "racer",
            "model": "gpt-4o",
            "provider": "OpenAI",
            "tokens_input": 1,
            "tokens_output": 1,
        },
    )
    assert calls["n"] == 1
    body = resp.json()
    assert body["processed"] == 1
    assert body["errored"] == 0
    assert authed_client.get("/api/v1/events").json()["total"] == 2
