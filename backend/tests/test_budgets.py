import pytest


def test_create_budget(authed_client):
    resp = authed_client.post(
        "/api/v1/budgets",
        json={
            "name": "Daily Limit",
            "scope": "global",
            "period": "daily",
            "limit_amount": 100.0,
        },
    )
    assert resp.status_code == 201


def test_list_budgets_with_spend(authed_client):
    authed_client.post(
        "/api/v1/budgets",
        json={
            "name": "Test Budget",
            "scope": "global",
            "period": "daily",
            "limit_amount": 50.0,
        },
    )
    # Ingest an event to create some spend
    authed_client.post(
        "/api/v1/events",
        json={
            "agent_name": "bot",
            "model": "gpt-4o",
            "provider": "OpenAI",
            "tokens_input": 1000,
            "tokens_output": 500,
            "cost": 0.05,
        },
    )
    resp = authed_client.get("/api/v1/budgets")
    assert resp.status_code == 200
    budgets = resp.json()
    assert len(budgets) >= 1
    assert budgets[0]["current_spend"] >= 0


def _spend(client, cost=1.0, agent="bot", **extra):
    return client.post(
        "/api/v1/events",
        json={
            "agent_name": agent,
            "model": "gpt-4o",
            "provider": "OpenAI",
            "tokens_input": 1000,
            "tokens_output": 500,
            "cost": cost,
            **extra,
        },
    ).json()


@pytest.mark.parametrize("action", ["block", "stop", "throttle"])
def test_exhausted_blocking_budget_records_and_flags_spend(authed_client, action):
    """Incurred spend is never discarded (it used to be rejected for 'stop',
    and 'block'/'throttle' from the UI did nothing at all)."""
    authed_client.post(
        "/api/v1/budgets",
        json={
            "name": "Tiny Budget",
            "scope": "global",
            "period": "daily",
            "limit_amount": 0.001,
            "control_action": action,
        },
    )
    first = _spend(authed_client)
    assert first["processed"] == 1 and first["over_budget"] == 0

    second = _spend(authed_client)
    assert second["processed"] == 1
    assert second["rejected"] == 0
    assert second["over_budget"] == 1
    assert "Tiny Budget" in second["budget_violations"][0]

    items = authed_client.get("/api/v1/events").json()["items"]
    assert sorted(i["over_budget"] for i in items) == [False, True]
    budget = authed_client.get("/api/v1/budgets").json()[0]
    assert budget["current_spend"] == pytest.approx(2.0)  # both calls counted


@pytest.mark.parametrize("action", ["block", "stop", "throttle"])
def test_precall_check_blocks_once_exhausted(authed_client, action):
    authed_client.post(
        "/api/v1/budgets",
        json={
            "name": "cap",
            "scope": "global",
            "period": "daily",
            "limit_amount": 1.5,
            "control_action": action,
        },
    )
    assert authed_client.get("/api/v1/budgets/check").json()["allowed"] is True
    _spend(authed_client, cost=1.0)
    assert authed_client.get("/api/v1/budgets/check").json()["allowed"] is True
    _spend(authed_client, cost=1.0)
    body = authed_client.get("/api/v1/budgets/check").json()
    assert body["allowed"] is False
    assert body["violations"][0]["name"] == "cap"
    assert body["violations"][0]["spent"] == pytest.approx(2.0)


def test_precall_check_respects_scope(authed_client):
    authed_client.post(
        "/api/v1/budgets",
        json={
            "name": "research cap",
            "scope": "agent",
            "scope_ref": "research",
            "period": "daily",
            "limit_amount": 1.0,
            "control_action": "block",
        },
    )
    _spend(authed_client, cost=5.0, agent="research")
    check = authed_client.get

    assert (
        check("/api/v1/budgets/check", params={"agent_name": "research"}).json()[
            "allowed"
        ]
        is False
    )
    assert (
        check("/api/v1/budgets/check", params={"agent_name": "writer"}).json()[
            "allowed"
        ]
        is True
    )
    # No attribution supplied: an agent-scoped budget cannot apply.
    assert check("/api/v1/budgets/check").json()["allowed"] is True


def test_alert_only_budget_never_blocks(authed_client):
    authed_client.post(
        "/api/v1/budgets",
        json={
            "name": "watch",
            "scope": "global",
            "period": "daily",
            "limit_amount": 0.001,
            "control_action": "alert",
        },
    )
    _spend(authed_client)
    assert _spend(authed_client)["over_budget"] == 0
    assert authed_client.get("/api/v1/budgets/check").json()["allowed"] is True


def test_budget_alert_only_does_not_block(authed_client):
    authed_client.post(
        "/api/v1/budgets",
        json={
            "name": "Alert Budget",
            "scope": "global",
            "period": "daily",
            "limit_amount": 0.001,
            "control_action": "alert",
        },
    )
    authed_client.post(
        "/api/v1/events",
        json={
            "agent_name": "bot",
            "model": "gpt-4o",
            "provider": "OpenAI",
            "tokens_input": 1000,
            "tokens_output": 500,
            "cost": 1.00,
        },
    )
    # This should NOT be rejected — alert-only budget
    resp = authed_client.post(
        "/api/v1/events",
        json={
            "agent_name": "bot",
            "model": "gpt-4o",
            "provider": "OpenAI",
            "tokens_input": 1000,
            "tokens_output": 500,
            "cost": 1.00,
        },
    )
    assert resp.json()["processed"] == 1
    assert resp.json()["rejected"] == 0
    assert resp.json()["over_budget"] == 0


@pytest.mark.parametrize(
    "override",
    [
        {"scope": "agnet"},  # typo used to silently become a global budget
        {"period": "fortnightly"},  # unknown period silently became daily
        {"limit_amount": -5},
        {"control_action": "nuke"},
        {"scope": "agent", "scope_ref": None},  # agent budget with no agent
        {"alert_thresholds": [0, 50]},
    ],
)
def test_invalid_budget_rejected(authed_client, override):
    body = {
        "name": "b",
        "scope": "agent",
        "scope_ref": "bot",
        "period": "daily",
        "limit_amount": 10,
    }
    body.update(override)
    assert authed_client.post("/api/v1/budgets", json=body).status_code == 422


def test_update_cannot_strip_scope_ref(authed_client):
    created = authed_client.post(
        "/api/v1/budgets",
        json={
            "name": "b",
            "scope": "agent",
            "scope_ref": "bot",
            "period": "daily",
            "limit_amount": 10,
        },
    ).json()
    resp = authed_client.put(f"/api/v1/budgets/{created['id']}", json={"scope_ref": ""})
    assert resp.status_code == 422


def test_negative_model_price_rejected(authed_client):
    providers = authed_client.get("/api/v1/providers").json()
    pid = providers[0]["id"]
    resp = authed_client.post(
        f"/api/v1/providers/{pid}/models",
        json={
            "model_name": "neg",
            "input_price_per_million": -1,
            "output_price_per_million": 1,
        },
    )
    assert resp.status_code == 422


def _ui_select_values():
    """Option values from the Budgets form, grouped by the <select> they sit in."""
    import pathlib
    import re

    tsx = (
        pathlib.Path(__file__).resolve().parents[2] / "frontend/src/pages/Budgets.tsx"
    ).read_text()
    groups = {}
    for label, body in re.findall(
        r'<label className="label">([^<]+)</label>\s*<select.*?>(.*?)</select>',
        tsx,
        flags=re.DOTALL,
    ):
        groups[label.strip()] = re.findall(r'<option value="([^"{]+)"', body)
    return groups


def test_every_budget_option_the_ui_offers_is_accepted(authed_client):
    """Regression: the UI offered scope provider/model and period hourly,
    which validation rejected (and which were never enforced)."""
    groups = _ui_select_values()
    assert groups["Scope"] and groups["Period"] and groups["Control Action"]
    for scope in groups["Scope"]:
        for period in groups["Period"]:
            for action in groups["Control Action"]:
                body = {
                    "name": f"{scope}-{period}-{action}",
                    "scope": scope,
                    "scope_ref": None if scope == "global" else "x",
                    "period": period,
                    "limit_amount": 10,
                    "control_action": action,
                }
                resp = authed_client.post("/api/v1/budgets", json=body)
                assert resp.status_code == 201, (body, resp.text)


@pytest.mark.parametrize(
    "scope,ref,event,matches",
    [
        ("provider", "OpenAI", {"provider": "openai"}, True),  # SDK spelling
        ("provider", "OpenAI", {"provider": "anthropic"}, False),
        ("model", "gpt-4o", {"model": "GPT-4o"}, True),
        ("model", "gpt-4o", {"model": "gpt-4o-mini"}, False),
    ],
)
def test_provider_and_model_scoped_budgets_count_matching_spend(
    authed_client, scope, ref, event, matches
):
    authed_client.post(
        "/api/v1/budgets",
        json={
            "name": "scoped",
            "scope": scope,
            "scope_ref": ref,
            "period": "hourly",
            "limit_amount": 100,
        },
    )
    ev = {"agent_name": "a", "model": "gpt-4o", "provider": "OpenAI"}
    ev.update(event)
    authed_client.post(
        "/api/v1/events",
        json={**ev, "tokens_input": 1, "tokens_output": 1, "cost": 3.0},
    )
    spend = authed_client.get("/api/v1/budgets").json()[0]["current_spend"]
    assert spend == (3.0 if matches else 0.0)


def _cap(client, limit=1.0, action="block", **extra):
    body = {
        "name": "cap",
        "scope": "global",
        "period": "daily",
        "limit_amount": limit,
        "control_action": action,
        **extra,
    }
    assert client.post("/api/v1/budgets", json=body).status_code == 201


def test_backdated_event_is_not_flagged_against_todays_budget(authed_client):
    """A replayed event from an earlier period never saw today's exhausted
    budget; flagging it would contradict /budgets/check."""
    from datetime import timedelta

    from app.timeutils import utcnow

    _cap(authed_client)
    _spend(authed_client, cost=5.0)  # today: exhausted
    old = (utcnow() - timedelta(days=3)).isoformat() + "Z"
    assert _spend(authed_client, cost=1.0, timestamp=old)["over_budget"] == 0
    assert _spend(authed_client, cost=1.0)["over_budget"] == 1


def test_backdated_cost_does_not_exhaust_todays_budget_within_a_batch(
    authed_client,
):
    from datetime import timedelta

    from app.timeutils import utcnow

    _cap(authed_client)
    old = (utcnow() - timedelta(days=3)).isoformat() + "Z"
    base = {"agent_name": "bot", "model": "gpt-4o", "provider": "OpenAI"}
    batch = [
        {**base, "tokens_input": 1, "tokens_output": 1, "cost": 5.0, "timestamp": old},
        {**base, "tokens_input": 1, "tokens_output": 1, "cost": 0.1},
    ]
    data = authed_client.post("/api/v1/events", json=batch).json()
    assert data["processed"] == 2 and data["over_budget"] == 0
    assert authed_client.get("/api/v1/budgets/check").json()["allowed"] is True


def test_event_that_fails_to_store_does_not_count_toward_the_batch(
    authed_client, monkeypatch
):
    import app.api.events as events_api

    real = events_api._process_event

    def flaky(event, db, cost, cost_source=None):
        if cost >= 5.0:
            raise RuntimeError("storage failed")
        return real(event, db, cost, cost_source)

    monkeypatch.setattr(events_api, "_process_event", flaky)
    _cap(authed_client)
    base = {"agent_name": "bot", "model": "gpt-4o", "provider": "OpenAI"}
    batch = [
        {**base, "tokens_input": 1, "tokens_output": 1, "cost": 5.0},
        {**base, "tokens_input": 1, "tokens_output": 1, "cost": 0.1},
    ]
    data = authed_client.post("/api/v1/events", json=batch).json()
    assert data["errored"] == 1 and data["processed"] == 1
    assert data["over_budget"] == 0
    assert data["budget_violations"] == []


def test_legacy_scoped_budget_without_ref_is_consistent(authed_client):
    """Rows created before validation (scope='agent', scope_ref NULL) count
    global spend; ingestion and the pre-call check must agree on them."""
    from app.models.models import Budget
    from tests.conftest import TestSession

    db = TestSession()
    db.add(
        Budget(
            name="legacy",
            scope="agent",
            scope_ref=None,
            period="daily",
            limit_amount=1.0,
            control_action="stop",
        )
    )
    db.commit()
    db.close()
    _spend(authed_client, cost=2.0, agent="anyone")
    flagged = _spend(authed_client, cost=1.0, agent="anyone")["over_budget"] == 1
    blocked = (
        authed_client.get(
            "/api/v1/budgets/check", params={"agent_name": "anyone"}
        ).json()["allowed"]
        is False
    )
    assert flagged and blocked


def test_budget_stops_blocking_when_raised(authed_client):
    _cap(authed_client, limit=1.0)
    _spend(authed_client, cost=2.0)
    assert authed_client.get("/api/v1/budgets/check").json()["allowed"] is False
    bid = authed_client.get("/api/v1/budgets").json()[0]["id"]
    authed_client.put(f"/api/v1/budgets/{bid}", json={"limit_amount": 10.0})
    assert authed_client.get("/api/v1/budgets/check").json()["allowed"] is True


def test_precall_check_matches_model_and_provider_scopes(authed_client):
    authed_client.post(
        "/api/v1/budgets",
        json={
            "name": "gpt-4o cap",
            "scope": "model",
            "scope_ref": "gpt-4o",
            "period": "daily",
            "limit_amount": 1.0,
            "control_action": "block",
        },
    )
    _spend(authed_client, cost=2.0)  # model gpt-4o
    check = authed_client.get
    assert (
        check("/api/v1/budgets/check", params={"model": "GPT-4o"}).json()["allowed"]
        is False
    )
    assert (
        check("/api/v1/budgets/check", params={"model": "gpt-4o-mini"}).json()[
            "allowed"
        ]
        is True
    )
    assert check("/api/v1/budgets/check").json()["allowed"] is True


def test_scope_ref_is_trimmed(authed_client):
    resp = authed_client.post(
        "/api/v1/budgets",
        json={
            "name": " cap ",
            "scope": "provider",
            "scope_ref": "OpenAI ",
            "period": "daily",
            "limit_amount": 10,
        },
    )
    assert resp.json()["scope_ref"] == "OpenAI"
    assert resp.json()["name"] == "cap"


def test_blank_budget_name_is_rejected(authed_client):
    resp = authed_client.post(
        "/api/v1/budgets",
        json={"name": "   ", "scope": "global", "period": "daily", "limit_amount": 1},
    )
    assert resp.status_code == 422
