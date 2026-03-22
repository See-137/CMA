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
