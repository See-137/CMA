"""Inputs and credentials must fail cleanly; status endpoints must tell the truth."""

import base64
import hashlib
import json
import logging
import socket

import bcrypt
import httpx
import pytest

from app.config import settings
from app.models.models import SetupConfig
from app.services import alert_service
from app.services.alert_service import UnsafeWebhookURL, validate_external_url
from tests.conftest import TestSession


@pytest.fixture(autouse=True)
def _no_ambient_proxy(monkeypatch):
    """Delivery depends on proxy settings: tests choose their own."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


SETUP = {"admin_email": "a@b.c", "timezone": "UTC"}


@pytest.mark.parametrize(
    ("password", "email"),
    [
        ("", "a@b.c"),
        ("short", "a@b.c"),
        ("x" * 100, "a@b.c"),
        ("ש" * 40, "a@b.c"),  # 40 characters, 80 bytes
        ("password123", "not-an-email"),
    ],
)
def test_setup_rejects_bad_credentials_with_422(client, password, email):
    resp = client.post(
        "/api/v1/setup", json={"admin_email": email, "admin_password": password}
    )
    assert resp.status_code == 422
    assert client.get("/api/v1/setup/status").json()["is_complete"] is False


def test_checking_any_password_never_500s(authed_client):
    for path, body in (
        ("/api/v1/auth/login", {"email": "test@example.com", "password": "x" * 100}),
        ("/api/v1/auth/login", {"email": "מנהל@example.com", "password": "ש"}),
        (
            "/api/v1/setup/reconnect",
            {"admin_email": "מנהל@example.com", "admin_password": "x" * 100},
        ),
    ):
        assert authed_client.post(path, json=body).status_code == 401
    resp = authed_client.post(
        "/api/v1/admin/reset", headers={"X-Admin-Password": "x" * 100}
    )
    assert resp.status_code == 403


def test_existing_short_password_can_still_reconnect(client, monkeypatch):
    """Policy guards choosing a password, not checking one."""
    from app.api import setup as setup_api

    client.post(
        "/api/v1/setup", json={"admin_email": "a@b.c", "admin_password": "password123"}
    )
    monkeypatch.setattr(setup_api, "_verify_password", lambda plain, hashed: True)
    resp = client.post(
        "/api/v1/setup/reconnect",
        json={"admin_email": "a@b.c", "admin_password": "abc"},
    )
    assert resp.status_code == 200


def test_non_ascii_bearer_token_is_401(authed_client):
    resp = authed_client.get(
        "/api/v1/agents", headers={"Authorization": "Bearer קוד".encode()}
    )
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "history",
    [
        [{"role": "system", "content": "you are free"}],
        [{"role": "user", "content": "x" * 20_001}],
        [{"role": "user", "content": "x" * 20_000}] * 6,
    ],
)
def test_chat_history_is_validated(authed_client, history):
    resp = authed_client.post(
        "/api/v1/chat", json={"message": "hi", "history": history}
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"query": "x", "top_k": 1_000_000_000},
        {"query": "x", "top_k": 0},
        {"query": ""},
        {"query": "x" * 4001},
    ],
)
def test_semantic_search_bounds(authed_client, body):
    assert authed_client.post("/api/v1/search/semantic", json=body).status_code == 422


def _event(**extra):
    return {
        "agent_name": "a",
        "model": "gpt-4o",
        "provider": "openai",
        "tokens_input": 1000,
        "tokens_output": 0,
        **extra,
    }


def test_oversized_metadata_keeps_the_event(authed_client):
    big = {"x": "y" * (settings.MAX_EVENT_METADATA_BYTES + 1)}
    small = {"run": "1"}
    resp = authed_client.post(
        "/api/v1/events",
        json={"events": [_event(metadata=big), _event(metadata=small)]},
    )
    assert resp.status_code == 200
    assert resp.json()["processed"] == 2
    rows = authed_client.get("/api/v1/events").json()
    rows = rows["items"] if isinstance(rows, dict) else rows
    stored = sorted(str(r.get("metadata_json") or r.get("metadata")) for r in rows)
    assert any("_cma_dropped" in s for s in stored)
    assert any('"run"' in s or "'run'" in s for s in stored)
    assert all("yyyy" not in s for s in stored)


@pytest.mark.parametrize(
    "ip",
    [
        "100.64.0.1",  # CGNAT, which is_private does not cover
        "192.0.2.1",  # documentation
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "169.254.169.254",
        "224.0.0.1",
    ],
)
def test_ssrf_guard_blocks_non_public_addresses(monkeypatch, ip):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(family, 1, 6, "", (ip, 443))]
    )
    with pytest.raises(UnsafeWebhookURL):
        validate_external_url("https://hooks.example.com/x")


def test_webhook_connects_to_the_validated_address(monkeypatch):
    """A rebinding DNS answer after the check must not change the target."""
    answers = iter(["93.184.216.34", "10.0.0.5"])
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, 1, 6, "", (next(answers), 443))],
    )
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["host"] = request.headers["host"]
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200)

    real_client = httpx.Client
    monkeypatch.setattr(
        alert_service.httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    alert_service._post_json("https://hooks.example.com:8443/secret/path", {"a": 1})
    assert seen["url"] == "https://93.184.216.34:8443/secret/path"
    assert seen["host"] == "hooks.example.com:8443"
    assert seen["sni"] == "hooks.example.com"


def _mock_transport(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(
        alert_service.httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )


def test_webhook_falls_back_across_validated_addresses(monkeypatch):
    """Dual-stack host with no IPv6 route: the IPv4 answer still gets the alert."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET6, 1, 6, "", ("2606:4700::1111", 443, 0, 0)),
            (socket.AF_INET, 1, 6, "", ("93.184.216.34", 443)),
        ],
    )
    tried = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(request.url.host)
        if ":" in request.url.host:
            raise httpx.ConnectError("no route", request=request)
        return httpx.Response(200)

    _mock_transport(monkeypatch, handler)
    alert_service._post_json("https://hooks.example.com/x", {"a": 1})
    assert tried == ["2606:4700::1111", "93.184.216.34"]


def test_webhook_does_not_resend_after_a_server_response(monkeypatch):
    """Only a failed connection moves on: a 500 must not deliver the alert twice."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, 1, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, 1, 6, "", ("93.184.216.35", 443)),
        ],
    )
    tried = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(request.url.host)
        return httpx.Response(500)

    _mock_transport(monkeypatch, handler)
    with pytest.raises(alert_service.WebhookFailed):
        alert_service._post_json("https://hooks.example.com/x", {"a": 1})
    assert tried == ["93.184.216.34"]


def test_webhook_raises_when_no_address_connects(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, 1, 6, "", ("93.184.216.34", 443))],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _mock_transport(monkeypatch, handler)
    with pytest.raises(alert_service.WebhookFailed):
        alert_service._post_json("https://hooks.example.com/x", {"a": 1})


def test_webhook_keeps_url_credentials_when_pinned(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, 1, 6, "", ("93.184.216.34", 443))],
    )
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200)

    _mock_transport(monkeypatch, handler)
    alert_service._post_json("https://us%40er:p%3Ass@hooks.example.com/x", {"a": 1})
    expected = base64.b64encode(b"us@er:p:ss").decode()
    assert seen["auth"] == f"Basic {expected}"
    assert "us" not in seen["url"].split("/")[2]


def test_channel_responses_mask_webhook_secrets(authed_client, monkeypatch):
    monkeypatch.setattr(
        alert_service, "_resolve_allowed", lambda url: ("hooks.slack.com", ["1.1.1.1"])
    )
    created = authed_client.post(
        "/api/v1/alerts/channels",
        json={
            "name": "s",
            "channel_type": "slack",
            "config": json.dumps(
                {"webhook_url": "https://hooks.slack.com/services/T0/B0/SECRET"}
            ),
        },
    )
    assert created.status_code == 201
    listed = authed_client.get("/api/v1/alerts/channels").text
    assert "SECRET" not in created.text
    assert "SECRET" not in listed
    assert "hooks.slack.com" in listed


@pytest.mark.parametrize("channel_type", ["email", "pagerduty", "carrier-pigeon"])
def test_channels_that_cannot_deliver_are_refused(authed_client, channel_type):
    resp = authed_client.post(
        "/api/v1/alerts/channels",
        json={"name": "x", "channel_type": channel_type, "config": "{}"},
    )
    assert resp.status_code == 422


def test_chat_status_reports_unreachable_llm_and_failed_store(
    authed_client, monkeypatch
):
    from app.api import chat

    def boom(*a, **k):
        raise httpx.ConnectError("nope")

    def store_down():
        raise RuntimeError("down")

    monkeypatch.setattr(chat.settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(chat.httpx, "get", boom)
    monkeypatch.setattr(chat, "get_events_collection", store_down)
    body = authed_client.get("/api/v1/chat/status").json()
    assert body["llm_available"] is False
    assert "not reachable" in body["llm_detail"]
    assert body["vector_store"] == "unavailable"


@pytest.mark.parametrize(
    ("models", "available"),
    [([{"name": "llama3:latest"}], True), ([{"name": "mistral:latest"}], False)],
)
def test_chat_status_checks_the_configured_ollama_model(
    authed_client, monkeypatch, models, available
):
    from app.api import chat

    monkeypatch.setattr(chat.settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(chat.settings, "LLM_MODEL", "llama3")
    monkeypatch.setattr(
        chat.httpx,
        "get",
        lambda *a, **k: httpx.Response(
            200, json={"models": models}, request=httpx.Request("GET", "http://x")
        ),
    )
    assert authed_client.get("/api/v1/chat/status").json()["llm_available"] is available


def test_admin_status_reads_the_database_in_use(tmp_path, authed_client):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.api.admin import _database_size_mb

    path = tmp_path / "other.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE t (x BLOB)")
        conn.exec_driver_sql("INSERT INTO t VALUES (zeroblob(3000000))")
    with Session(engine) as db:
        assert _database_size_mb(db) > 2
    assert authed_client.get("/api/v1/admin/status").status_code == 200


def test_no_baseline_means_no_percentage(authed_client):
    authed_client.post("/api/v1/events", json=_event())
    body = authed_client.get("/api/v1/dashboard/overview").json()
    assert body["cost_change_pct"] is None
    assert body["request_change_pct"] is None


@pytest.mark.parametrize(
    "ip",
    [
        "64:ff9b::a00:1",  # NAT64 to 10.0.0.1
        "64:ff9b::a9fe:a9fe",  # NAT64 to the metadata address
        "::127.0.0.1",
        "4000::1",
        "::ffff:0:7f00:1",
        "fec0::1",
        "2002:a00:1::1",  # 6to4 of 10.0.0.1
    ],
)
def test_ssrf_guard_blocks_ipv6_forms_that_reach_internal_addresses(ip):
    with pytest.raises(UnsafeWebhookURL):
        validate_external_url(f"https://[{ip}]/x")


@pytest.mark.parametrize("url", [123, "http://[oops", "https://h:notaport/x"])
def test_malformed_webhook_url_is_a_422(authed_client, url):
    resp = authed_client.post(
        "/api/v1/alerts/channels",
        json={
            "name": "c",
            "channel_type": "webhook",
            "config": json.dumps({"url": url}),
        },
    )
    assert resp.status_code == 422


def test_bad_stored_values_cannot_break_the_channel_list(authed_client):
    config = {
        "note": "http://[oops",
        "nested": {"url": "https://h.example.com/secret-path"},
        "token": "abc",
        "other": ["https://h.example.com/secret-list"],
    }
    resp = authed_client.post(
        "/api/v1/alerts/channels",
        json={"name": "c", "channel_type": "in_app", "config": json.dumps(config)},
    )
    assert resp.status_code in (200, 201)
    listed = authed_client.get("/api/v1/alerts/channels")
    assert listed.status_code == 200
    item = next(c for c in listed.json() if c["name"] == "c")
    masked = json.loads(item["config_json"])
    assert masked["token"] == "***"
    assert masked["note"] == "***"
    assert masked["nested"]["url"] == "https://h.example.com/***"
    assert masked["other"] == ["https://h.example.com/***"]


@pytest.mark.parametrize(
    ("config", "hidden"),
    [
        ({"auth": {"user": "U1", "pass": "PW1"}}, "PW1"),
        ({"token": 123456}, "123456"),
        ({"secret": [{"x": "L2"}]}, "L2"),
        ({"X-Api-Key": "K1"}, "K1"),
        ({"pwd": "K2"}, "K2"),
        ({"credential": "K3"}, "K3"),
        ({"hook": "hooks.slack.com/services/T0/SEC"}, "SEC"),
    ],
)
def test_secret_values_are_hidden_whatever_their_shape(authed_client, config, hidden):
    authed_client.post(
        "/api/v1/alerts/channels",
        json={"name": "c", "channel_type": "in_app", "config": json.dumps(config)},
    )
    assert hidden not in authed_client.get("/api/v1/alerts/channels").text


def test_webhook_secret_stays_out_of_logs_on_failure_and_success(monkeypatch, caplog):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, 1, 6, "", ("93.184.216.34", 443))],
    )
    status = {"code": 404}
    _mock_transport(monkeypatch, lambda request: httpx.Response(status["code"]))
    url = "https://hooks.slack.com/services/T0/B0/SUPERSECRET"
    alert = alert_service.Alert(
        id=1, alert_type="t", severity="info", message="m", is_resolved=False
    )

    class Channels:
        def filter(self, *a):
            return self

        def all(self):
            return [
                alert_service.AlertChannel(
                    id=1,
                    channel_type="slack",
                    config_json=json.dumps({"webhook_url": url}),
                )
            ]

    class Db:
        def query(self, *a):
            return Channels()

    with caplog.at_level("DEBUG"):
        alert_service.dispatch_alert(alert, Db())
        status["code"] = 200
        alert_service.dispatch_alert(alert, Db())
    joined = "\n".join(
        r.getMessage() + (str(r.exc_info[1]) if r.exc_info else "")
        for r in caplog.records
        if r.name.startswith("app.")
    )
    assert "delivered" in joined
    assert "hooks.slack.com returned HTTP 404" in joined
    assert "SUPERSECRET" not in joined


def test_transport_failure_names_the_reason_not_the_secret(monkeypatch, caplog):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, 1, 6, "", ("93.184.216.34", 443))],
    )

    def handler(request):
        raise httpx.ConnectError(f"CERTIFICATE_VERIFY_FAILED for {request.url}")

    _mock_transport(monkeypatch, handler)
    with pytest.raises(alert_service.WebhookFailed) as err:
        alert_service._post_json("https://h.example.com/T0/SECRET", {"a": 1})
    assert "CERTIFICATE_VERIFY_FAILED" in str(err.value)
    assert "SECRET" not in str(err.value)
    assert err.value.__cause__ is None
    assert err.value.__suppress_context__ is True


def test_httpx_request_logging_is_off():
    from app.logging_config import setup_logging

    setup_logging()
    assert logging.getLogger("httpx").getEffectiveLevel() > logging.INFO


def test_a_redirect_is_not_a_delivery(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, 1, 6, "", ("93.184.216.34", 443))],
    )
    _mock_transport(
        monkeypatch,
        lambda request: httpx.Response(302, headers={"location": "http://10.0.0.1/"}),
    )
    with pytest.raises(alert_service.WebhookFailed):
        alert_service._post_json("https://h.example.com/x", {"a": 1})


def test_direct_calls_ignore_proxy_env_but_keep_ca_env(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, 1, 6, "", ("93.184.216.34", 443))],
    )
    seen = {}
    real_client = httpx.Client

    def client(**kw):
        seen.update(trust_env=kw["trust_env"], verify=kw["verify"])
        kw.pop("verify")
        return real_client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200)), **kw
        )

    made = []
    monkeypatch.setattr(alert_service.httpx, "Client", client)
    monkeypatch.setattr(
        alert_service.httpx,
        "create_ssl_context",
        lambda trust_env: made.append(trust_env) or "ctx",
    )
    alert_service._post_json("https://h.example.com/x", {"a": 1})
    assert seen == {"trust_env": False, "verify": "ctx"}
    assert made == [True]


def test_all_proxy_counts_as_a_proxy(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "http://proxy.internal:3128")
    assert alert_service._uses_proxy("https", "h.example.com") is True


def test_model_default_follows_the_provider(monkeypatch):
    from app.config import Settings

    for name in ("CMA_LLM_MODEL", "CMA_LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    assert Settings(_env_file=None, LLM_PROVIDER="openai").LLM_MODEL == "gpt-6-luna"
    assert Settings(_env_file=None, LLM_PROVIDER="ollama").LLM_MODEL == "llama3"
    assert (
        Settings(_env_file=None, LLM_PROVIDER="openai", LLM_MODEL="x").LLM_MODEL == "x"
    )


def test_a_422_for_nan_is_valid_json(client):
    resp = client.post(
        "/api/v1/setup",
        content='{"admin_email":NaN,"admin_password":"testpass123"}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422
    json.loads(resp.text, parse_constant=lambda c: pytest.fail(c))


def test_webhook_behind_a_proxy_keeps_the_host_name(monkeypatch):
    """A CONNECT to the pinned IP would fail TLS: the proxy must get the name."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, 1, 6, "", ("93.184.216.34", 443))],
    )
    seen = {}

    class Client:
        def __init__(self, **kw):
            seen["trust_env"] = kw["trust_env"]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json):
            seen["url"] = url
            return httpx.Response(200)

    monkeypatch.setattr(alert_service.httpx, "Client", Client)
    alert_service._post_json("https://hooks.example.com/x", {"a": 1})
    assert seen == {"trust_env": True, "url": "https://hooks.example.com/x"}


def test_proxy_bypass_and_no_proxy_stay_pinned(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    monkeypatch.setenv("NO_PROXY", "hooks.example.com")
    assert alert_service._uses_proxy("https", "hooks.example.com") is False
    assert alert_service._uses_proxy("https", "other.example.com") is True
    assert alert_service._uses_proxy("http", "other.example.com") is False


def test_internationalised_webhook_host_is_sent_as_idna(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, 1, 6, "", ("93.184.216.34", 443))],
    )
    seen = {}

    def handler(request):
        seen["host"] = request.headers["host"]
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200)

    _mock_transport(monkeypatch, handler)
    alert_service._post_json("https://b\u00fccher.example.com/x", {"a": 1})
    assert seen == {
        "host": "xn--bcher-kva.example.com",
        "sni": "xn--bcher-kva.example.com",
    }


def test_admin_with_a_long_password_from_bcrypt_4_can_still_log_in(authed_client):
    password = "p" * 80
    stored = bcrypt.hashpw(password[:72].encode(), bcrypt.gensalt()).decode()
    with TestSession() as db:
        config = db.query(SetupConfig).first()
        config.admin_password_hash = stored
        db.commit()
        email = config.admin_email
    resp = authed_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200
    resp = authed_client.post(
        "/api/v1/auth/login", json={"email": email, "password": "wrong" * 20}
    )
    assert resp.status_code == 401


def test_legacy_sha256_password_over_72_bytes_keeps_its_hash(authed_client):
    password = "q" * 80
    legacy = hashlib.sha256(password.encode()).hexdigest()
    with TestSession() as db:
        config = db.query(SetupConfig).first()
        config.admin_password_hash = legacy
        db.commit()
        email = config.admin_email
    resp = authed_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200
    with TestSession() as db:
        assert db.query(SetupConfig).first().admin_password_hash == legacy


def test_lone_surrogates_never_500(client):
    body = '{"admin_email":"a@b.c","admin_password":"\\ud800aaaaaaaa"}'
    headers = {"content-type": "application/json"}
    assert (
        client.post("/api/v1/setup", content=body, headers=headers).status_code == 422
    )
    client.post(
        "/api/v1/setup", json={"admin_email": "a@b.c", "admin_password": "testpass123"}
    )
    login = '{"email":"a@b.c","password":"\\ud800"}'
    resp = client.post("/api/v1/auth/login", content=login, headers=headers)
    assert resp.status_code == 401


def test_login_and_reconnect_trim_the_email_as_setup_does(client):
    client.post(
        "/api/v1/setup",
        json={"admin_email": "a@b.co ", "admin_password": "testpass123"},
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "a@b.co ", "password": "testpass123"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/setup/reconnect",
            json={"admin_email": " a@b.co", "admin_password": "testpass123"},
        ).status_code
        == 200
    )


@pytest.mark.parametrize("payload", [{"models": None}, {"models": 5}, [1]])
def test_chat_status_survives_odd_ollama_answers(authed_client, monkeypatch, payload):
    from app.api import chat

    monkeypatch.setattr(chat.settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(
        chat.httpx,
        "get",
        lambda *a, **k: httpx.Response(
            200, json=payload, request=httpx.Request("GET", "http://o")
        ),
    )
    resp = authed_client.get("/api/v1/chat/status")
    assert resp.status_code == 200
    assert resp.json()["llm_available"] is False


def test_no_change_on_an_empty_period_is_not_called_new():
    from app.api.dashboard import _pct_change

    assert _pct_change(0, 0) == 0.0
    assert _pct_change(5, 0) is None
