def test_login_success(client):
    client.post(
        "/api/v1/setup",
        json={
            "admin_email": "admin@test.com",
            "admin_password": "secure123",
        },
    )
    resp = client.post(
        "/api/v1/auth/login",
        json={
            "email": "admin@test.com",
            "password": "secure123",
        },
    )
    assert resp.status_code == 200
    assert "api_key" in resp.json()


def test_login_wrong_password(client):
    client.post(
        "/api/v1/setup",
        json={
            "admin_email": "admin@test.com",
            "admin_password": "secure123",
        },
    )
    resp = client.post(
        "/api/v1/auth/login",
        json={
            "email": "admin@test.com",
            "password": "wrongpass",
        },
    )
    assert resp.status_code == 401


def test_protected_route_without_auth(client):
    client.post(
        "/api/v1/setup",
        json={
            "admin_email": "a@b.com",
            "admin_password": "pass123456",
        },
    )
    resp = client.get("/api/v1/agents")
    assert resp.status_code == 401


def test_protected_route_with_auth(authed_client):
    resp = authed_client.get("/api/v1/agents")
    assert resp.status_code == 200
