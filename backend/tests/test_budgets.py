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


def test_budget_enforcement_stop(authed_client):
    # Create a very low budget with stop action
    authed_client.post(
        "/api/v1/budgets",
        json={
            "name": "Tiny Budget",
            "scope": "global",
            "period": "daily",
            "limit_amount": 0.001,
            "control_action": "stop",
        },
    )
    # First event should process (spend starts at 0)
    resp1 = authed_client.post(
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
    assert resp1.json()["processed"] == 1

    # Second event should be rejected (budget exceeded)
    resp2 = authed_client.post(
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
    assert resp2.json()["rejected"] == 1
    assert len(resp2.json()["rejection_reasons"]) > 0


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
