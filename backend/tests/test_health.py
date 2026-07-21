"""Spec for liveness/readiness split (PR D).

Contract under test:
- /health is pure liveness: process up -> 200, no dependency checks, no
  auth. A liveness probe that checks dependencies causes restart storms.
- /health/ready is readiness with the architecture decision encoded as
  HTTP semantics: Postgres unreachable -> 503 (SQL is the source of
  truth); vector store unreachable -> 200 with status "degraded"
  (ChromaDB is a rebuildable cache — RAG quality drops, the service
  does not die).
- Neither endpoint appears in the HTTP RED metrics: orchestrator probes
  every few seconds would drown real traffic.
"""

from __future__ import annotations

from prometheus_client.parser import text_string_to_metric_families


def test_liveness_is_unconditional(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ready_ok_without_auth(client):
    """Readiness needs no credentials — orchestrator probes can't hold any."""
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["checks"]["database"] == "ok"


def test_ready_hard_fails_on_dead_database(client, monkeypatch):
    import app.api.health as health_api

    def _boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(health_api, "_check_database", _boom)

    resp = client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "unreachable"


def test_ready_degrades_on_dead_vector_store(client, monkeypatch):
    """The cache being down is a quality problem, not an availability
    problem — readiness must say 'degraded', not take the service out of
    rotation."""
    import app.api.health as health_api

    def _boom():
        raise RuntimeError("chroma unreachable")

    monkeypatch.setattr(health_api, "_check_vector_store", _boom)

    resp = client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["vector_store"] == "degraded"
    assert body["checks"]["database"] == "ok"


def test_ready_fails_fast_when_database_hangs(client, monkeypatch):
    """A WEDGED database (TCP up, query black-holed) must not hang the
    probe: without a bound, every 30s healthcheck would pin a thread and
    pool slot forever until the shared pool starves real traffic —
    Docker's timeout only kills the probe client, never the server call.
    Regression test for the review finding."""
    import time

    import app.api.health as health_api

    monkeypatch.setattr(health_api, "_CHECK_TIMEOUT_SECONDS", 1.0)

    def _wedged():
        time.sleep(30)

    monkeypatch.setattr(health_api, "_check_database", _wedged)

    started = time.perf_counter()
    resp = client.get("/health/ready")
    elapsed = time.perf_counter() - started

    assert resp.status_code == 503
    assert resp.json()["checks"]["database"] == "unreachable"
    assert elapsed < 5, f"readiness took {elapsed:.1f}s against a wedged DB"


def test_health_endpoints_excluded_from_http_metrics(authed_client):
    authed_client.get("/health")
    authed_client.get("/health/ready")
    resp = authed_client.get("/metrics")
    assert resp.status_code == 200

    handlers = set()
    for family in text_string_to_metric_families(resp.text):
        for s in family.samples:
            h = s.labels.get("handler")
            if h:
                handlers.add(h)
    assert "/health" not in handlers
    assert "/health/ready" not in handlers
