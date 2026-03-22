

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
