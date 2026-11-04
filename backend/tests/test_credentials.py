"""D4: an ingest key cannot manage, a login gives a revocable session, and the
original shared key of an older install keeps working through its window."""

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, text

from app.models.models import AdminSession, IngestKey, SetupConfig
from app.services import credentials
from app.timeutils import utcnow
from tests.conftest import TestSession
from tests.test_migrations import _alembic

EVENT = {
    "agent_name": "a",
    "model": "gpt-4o",
    "provider": "openai",
    "tokens_input": 1,
    "tokens_output": 1,
}
ADMIN = {"X-Admin-Password": "testpass123"}


def _as(token):
    return {"Authorization": f"Bearer {token}"}


MANAGEMENT = [
    ("get", "/api/v1/events"),
    ("get", "/api/v1/agents"),
    ("get", "/api/v1/providers"),
    ("get", "/api/v1/budgets"),
    ("get", "/api/v1/alerts"),
    ("get", "/api/v1/dashboard/overview"),
    ("get", "/api/v1/admin/status"),
    ("get", "/api/v1/keys"),
    ("get", "/api/v1/chat/status"),
    ("post", "/api/v1/budgets"),
    ("post", "/api/v1/keys"),
]


def test_ingest_key_sends_events_and_checks_budgets(authed_client):
    headers = _as(authed_client.ingest_key)
    assert (
        authed_client.post("/api/v1/events", json=EVENT, headers=headers).status_code
        == 200
    )
    assert (
        authed_client.get("/api/v1/budgets/check", headers=headers).status_code == 200
    )


@pytest.mark.parametrize(("method", "path"), MANAGEMENT)
def test_ingest_key_is_refused_on_management(authed_client, method, path):
    resp = getattr(authed_client, method)
    kwargs = {"json": {}} if method == "post" else {}
    assert (
        resp(path, headers=_as(authed_client.ingest_key), **kwargs).status_code == 403
    )


@pytest.mark.parametrize(("method", "path"), MANAGEMENT)
def test_no_credential_is_refused_everywhere(client, method, path):
    client.post(
        "/api/v1/setup", json={"admin_email": "a@b.c", "admin_password": "testpass123"}
    )
    kwargs = {"json": {}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401


def test_every_route_needs_a_credential(client):
    """A route added without auth would otherwise go unnoticed."""
    client.post(
        "/api/v1/setup", json={"admin_email": "a@b.c", "admin_password": "testpass123"}
    )
    public = {
        "/api/v1/setup",
        "/api/v1/setup/status",
        "/api/v1/setup/reconnect",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
    }
    from app.main import app

    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1") or path in public:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            url = path.replace("{", "").replace("}", "")
            resp = client.request(method, url)
            assert resp.status_code in (401, 422, 404), (method, path, resp.status_code)
            if resp.status_code != 401:  # a parameter that failed before auth
                resp = client.request(method, path.split("{")[0] + "1")
                assert resp.status_code == 401, (method, path, resp.status_code)


def test_login_gives_a_session_that_logout_ends(authed_client):
    token = authed_client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
    ).json()["session_token"]
    assert authed_client.get("/api/v1/agents", headers=_as(token)).status_code == 200
    assert (
        authed_client.post("/api/v1/auth/logout", headers=_as(token)).status_code == 204
    )
    assert authed_client.get("/api/v1/agents", headers=_as(token)).status_code == 401


def test_expired_session_is_refused_and_cleared_at_next_login(authed_client):
    with TestSession() as db:
        token, _ = credentials.create_session(db)
        db.query(AdminSession).update({"expires_at": utcnow() - timedelta(minutes=1)})
        db.commit()
    assert authed_client.get("/api/v1/agents", headers=_as(token)).status_code == 401
    with TestSession() as db:
        credentials.create_session(db)
        assert db.query(AdminSession).count() == 1


def test_reconnect_gives_a_session_and_no_key(client):
    client.post(
        "/api/v1/setup", json={"admin_email": "a@b.c", "admin_password": "testpass123"}
    )
    body = client.post(
        "/api/v1/setup/reconnect",
        json={"admin_email": "a@b.c", "admin_password": "testpass123"},
    ).json()
    assert body["api_key"] is None
    assert body["session_token"].startswith("cmas_")


def test_new_install_has_no_usable_shared_key(client):
    client.post(
        "/api/v1/setup", json={"admin_email": "a@b.c", "admin_password": "testpass123"}
    )
    with TestSession() as db:
        shared = db.query(SetupConfig).first().api_key
    assert client.get("/api/v1/agents", headers=_as(shared)).status_code == 401
    assert (
        client.post("/api/v1/events", json=EVENT, headers=_as(shared)).status_code
        == 401
    )


def test_keys_are_stored_hashed(authed_client):
    created = authed_client.post("/api/v1/keys", json={"name": "agent"}).json()
    with TestSession() as db:
        stored = db.query(IngestKey).filter_by(id=created["id"]).one()
    assert created["key"] not in (stored.key_hash, stored.prefix)
    assert created["key"].startswith(stored.prefix)
    assert "key" not in authed_client.get("/api/v1/keys").json()[0]


def test_revoke_and_rotate(authed_client):
    created = authed_client.post("/api/v1/keys", json={"name": "agent"}).json()
    send = lambda key: (
        authed_client.post("/api/v1/events", json=EVENT, headers=_as(key)).status_code
    )
    assert send(created["key"]) == 200
    rotated = authed_client.post(f"/api/v1/keys/{created['id']}/rotate").json()
    assert rotated["name"] == "agent"
    assert send(created["key"]) == 401
    assert send(rotated["key"]) == 200
    authed_client.post(f"/api/v1/keys/{rotated['id']}/revoke")
    assert send(rotated["key"]) == 401
    assert authed_client.post("/api/v1/keys/99999/revoke").status_code == 404


def test_blank_key_name_is_refused(authed_client):
    assert authed_client.post("/api/v1/keys", json={"name": "  "}).status_code == 422


def test_last_used_is_recorded_without_a_write_per_request(authed_client):
    headers = _as(authed_client.ingest_key)
    authed_client.post("/api/v1/events", json=EVENT, headers=headers)
    with TestSession() as db:
        first = db.query(IngestKey).first().last_used_at
    assert first is not None
    authed_client.post("/api/v1/events", json=EVENT, headers=headers)
    with TestSession() as db:
        assert db.query(IngestKey).first().last_used_at == first


def _legacy(until_days):
    with TestSession() as db:
        config = db.query(SetupConfig).first()
        config.api_key = "old-shared-key"
        config.legacy_key_enabled = True
        config.legacy_full_until = utcnow() + timedelta(days=until_days)
        db.commit()


def test_shared_key_keeps_full_access_in_its_window_with_a_warning(
    authed_client, caplog
):
    _legacy(10)
    resp = authed_client.get("/api/v1/agents", headers=_as("old-shared-key"))
    assert resp.status_code == 200
    assert "X-CMA-Deprecated" in resp.headers
    status = authed_client.get("/api/v1/keys/status", headers=_as("old-shared-key"))
    assert status.json()["role"] == "shared_key"
    assert status.json()["shared_key_full_access_until"]


def test_shared_key_is_ingest_only_after_its_window(authed_client):
    _legacy(-1)
    key = _as("old-shared-key")
    assert (
        authed_client.post("/api/v1/events", json=EVENT, headers=key).status_code == 200
    )
    assert authed_client.get("/api/v1/agents", headers=key).status_code == 403


def test_retiring_the_shared_key_needs_the_password_and_ends_it(authed_client):
    _legacy(10)
    key = _as("old-shared-key")
    assert authed_client.post("/api/v1/keys/shared/retire").status_code == 403
    assert (
        authed_client.post("/api/v1/keys/shared/retire", headers=ADMIN).status_code
        == 200
    )
    assert (
        authed_client.post("/api/v1/events", json=EVENT, headers=key).status_code == 401
    )


def test_metrics_refuse_an_ingest_key(authed_client):
    resp = authed_client.get("/metrics", headers=_as(authed_client.ingest_key))
    assert resp.status_code == 403


def test_second_setup_is_refused(client):
    body = {"admin_email": "a@b.c", "admin_password": "testpass123"}
    assert client.post("/api/v1/setup", json=body).status_code == 200
    assert client.post("/api/v1/setup", json=body).status_code == 400


def test_a_second_setup_row_cannot_exist(client):
    from sqlalchemy.exc import IntegrityError

    client.post(
        "/api/v1/setup", json={"admin_email": "a@b.c", "admin_password": "testpass123"}
    )
    with TestSession() as db:
        db.add(
            SetupConfig(
                admin_email="x@y.z",
                admin_password_hash="h",
                api_key="k",
                is_setup_complete=True,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_full_reset_removes_keys_and_sessions(authed_client):
    assert (
        authed_client.post("/api/v1/admin/reset-full", headers=ADMIN).status_code == 200
    )
    with TestSession() as db:
        assert db.query(IngestKey).count() == 0
        assert db.query(AdminSession).count() == 0


def test_account_failures_are_limited_across_addresses(authed_client, monkeypatch):
    from app.services import rate_limit

    monkeypatch.setattr("app.api.auth.settings.RATE_LIMIT_LOGIN_PER_MINUTE", 2)
    monkeypatch.setattr(rate_limit, "_client_id", lambda r: str(id(object())))
    codes = {
        authed_client.post(
            "/api/v1/auth/login", json={"email": "x@y.z", "password": "bad"}
        ).status_code
        for _ in range(12)
    }
    assert codes == {401, 429}


def test_time_zone_can_be_changed_with_the_password(authed_client):
    body = {"timezone": "Asia/Jerusalem"}
    assert authed_client.put("/api/v1/admin/timezone", json=body).status_code == 403
    resp = authed_client.put("/api/v1/admin/timezone", json=body, headers=ADMIN)
    assert resp.status_code == 200
    with TestSession() as db:
        assert db.query(SetupConfig).first().timezone == "Asia/Jerusalem"
    resp = authed_client.put(
        "/api/v1/admin/timezone", json={"timezone": "Mars/Base"}, headers=ADMIN
    )
    assert resp.status_code == 422


def test_009_keeps_an_installs_key_working_for_90_days(tmp_path):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    _alembic(url, "upgrade", "008")
    eng = create_engine(url)
    with eng.begin() as c:
        for i in (1, 2):  # a past setup race left two rows
            c.execute(
                text(
                    "INSERT INTO setup_config (id, admin_email, admin_password_hash, "
                    "api_key, is_setup_complete) VALUES "
                    f"({i},'a@b.c','h','key{i}',1)"
                )
            )
    eng.dispose()
    _alembic(url, "upgrade", "009")
    eng = create_engine(url)
    with eng.connect() as c:
        rows = c.execute(
            text(
                "SELECT id, singleton, legacy_key_enabled, legacy_full_until "
                "FROM setup_config ORDER BY id"
            )
        ).all()
    eng.dispose()
    assert rows[0][1] == 1
    assert rows[1][1] is None
    assert rows[0][2] == 1
    assert rows[0][3] > str(utcnow() + timedelta(days=89))
    _alembic(url, "downgrade", "008")
