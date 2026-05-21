"""Regression tests for the end-to-end review fixes."""

import pytest

from app.services.alert_service import UnsafeWebhookURL, validate_external_url


def _event(agent="bot", tokens_in=100, tokens_out=50, **extra):
    return {
        "agent_name": agent,
        "model": "gpt-4o",
        "provider": "OpenAI",
        "tokens_input": tokens_in,
        "tokens_output": tokens_out,
        **extra,
    }


# -- SDK contract: {"events": [...]} envelope ---------------------------------


def test_ingest_accepts_events_envelope(authed_client):
    """The SDK posts {"events": [...]} — the endpoint must accept that shape."""
    resp = authed_client.post(
        "/api/v1/events",
        json={"events": [_event(agent="env-1"), _event(agent="env-2")]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["received"] == 2
    assert data["processed"] == 2


def test_embedder_receives_real_ids(authed_client, monkeypatch):
    """Background embedding must get materialised IDs, not a list of None."""
    captured: list[list[int]] = []

    import app.services.vector_store as vs

    monkeypatch.setattr(
        vs, "embed_and_upsert_events", lambda db, ids: captured.append(ids)
    )
    resp = authed_client.post(
        "/api/v1/events",
        json={"events": [_event(agent="id-1"), _event(agent="id-2")]},
    )
    assert resp.status_code == 200
    assert captured, "embedder was not invoked"
    ids = captured[0]
    assert len(ids) == 2
    assert all(isinstance(i, int) for i in ids)


def test_ingest_still_accepts_bare_array_and_object(authed_client):
    assert (
        authed_client.post("/api/v1/events", json=_event(agent="bare")).json()[
            "processed"
        ]
        == 1
    )
    assert (
        authed_client.post("/api/v1/events", json=[_event(agent="arr")]).json()[
            "processed"
        ]
        == 1
    )


# -- Input validation ---------------------------------------------------------


def test_negative_tokens_rejected(authed_client):
    resp = authed_client.post("/api/v1/events", json=_event(tokens_in=-5))
    assert resp.status_code == 422


def test_batch_size_cap(authed_client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "MAX_EVENTS_PER_REQUEST", 3)
    resp = authed_client.post(
        "/api/v1/events", json={"events": [_event() for _ in range(4)]}
    )
    assert resp.status_code == 413


# -- Batch-aware budget enforcement -------------------------------------------


def test_batch_enforcement_stops_within_one_request(authed_client):
    """A single batch must not be able to overshoot a hard budget limit."""
    # gpt-4o input is $2.50/M -> 1M input tokens = $2.50 per event.
    authed_client.post(
        "/api/v1/budgets",
        json={
            "name": "hard-cap",
            "scope": "global",
            "period": "daily",
            "limit_amount": 5.0,
            "control_action": "stop",
        },
    )
    batch = [_event(agent=f"b{i}", tokens_in=1_000_000, tokens_out=0) for i in range(5)]
    resp = authed_client.post("/api/v1/events", json={"events": batch})
    data = resp.json()
    # ~2 events ($5.00) accepted, the rest rejected by the running batch total.
    assert data["processed"] <= 2
    assert data["rejected"] >= 3
    assert data["rejection_reasons"]


# -- Admin scope: destructive ops require the admin password ------------------


def test_reset_requires_admin_password(authed_client):
    # API key alone (no X-Admin-Password) must be rejected.
    resp = authed_client.post("/api/v1/admin/reset")
    assert resp.status_code == 403


def test_reset_succeeds_with_admin_password(authed_client):
    resp = authed_client.post(
        "/api/v1/admin/reset", headers={"X-Admin-Password": "testpass123"}
    )
    assert resp.status_code == 200


def test_reset_rejects_wrong_admin_password(authed_client):
    resp = authed_client.post(
        "/api/v1/admin/reset", headers={"X-Admin-Password": "wrong"}
    )
    assert resp.status_code == 403


# -- SSRF guard on alert channels ---------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/api/v1/admin/reset-full",
        "http://127.0.0.1/x",
        "ftp://example.com/x",
    ],
)
def test_validate_external_url_blocks_internal(url):
    with pytest.raises(UnsafeWebhookURL):
        validate_external_url(url)


def test_create_webhook_channel_rejects_internal_url(authed_client):
    import json

    resp = authed_client.post(
        "/api/v1/alerts/channels",
        json={
            "name": "evil",
            "channel_type": "webhook",
            "config": json.dumps({"url": "http://127.0.0.1/x"}),
        },
    )
    assert resp.status_code == 422


# -- Login rate limiting ------------------------------------------------------


def test_login_is_rate_limited(authed_client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN_PER_MINUTE", 3)
    # The limiter resolves its limit at call time, so the override applies.
    last = None
    for _ in range(6):
        last = authed_client.post(
            "/api/v1/auth/login",
            json={"email": "test@example.com", "password": "wrong"},
        )
    assert last.status_code == 429
