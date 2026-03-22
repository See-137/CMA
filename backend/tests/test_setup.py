def test_setup_status_initially_incomplete(client):
    resp = client.get("/api/v1/setup/status")
    assert resp.status_code == 200
    assert resp.json()["is_complete"] is False


def test_setup_creates_admin(client):
    resp = client.post(
        "/api/v1/setup",
        json={
            "admin_email": "admin@test.com",
            "admin_password": "password123",
            "deployment_name": "TestCMA",
            "timezone": "UTC",
            "currency": "USD",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_complete"] is True
    assert "api_key" in data
    assert len(data["api_key"]) > 20


def test_setup_cannot_run_twice(client):
    client.post(
        "/api/v1/setup",
        json={
            "admin_email": "a@b.com",
            "admin_password": "password123",
        },
    )
    resp = client.post(
        "/api/v1/setup",
        json={
            "admin_email": "a@b.com",
            "admin_password": "password123",
        },
    )
    assert resp.status_code == 400


def test_setup_status_after_complete(client):
    client.post(
        "/api/v1/setup",
        json={
            "admin_email": "a@b.com",
            "admin_password": "password123",
            "deployment_name": "MyCMA",
        },
    )
    resp = client.get("/api/v1/setup/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_complete"] is True
    assert data["deployment_name"] == "MyCMA"
