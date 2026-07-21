"""Spec for CMA self-observability metrics (PR A).

Contract under test:
- /metrics is auth-gated: API key, or an optional metrics-scoped token
  (CMA_METRICS_TOKEN) so Prometheus configs don't hold the master key.
  Missing header -> 401; pre-setup with a key attempt -> 503 (documented).
- Domain metrics carry NO agent_name/model labels — cardinality discipline
  is part of the spec. The sweep seeds live samples first so it can never
  pass vacuously on an empty registry.
- The embedding-backlog gauge computes at scrape time behind a TTL cache
  (successes cached, failures never), clamps at zero, and a collector
  failure degrades to a scrape-error counter with the gauge OMITTED —
  never a 500, never a fabricated zero.
- Maintenance jobs are isolated (own sessions) with per-job success
  timestamps and failure counters; the job NAMES are a published contract
  (dashboards key on them), so they are asserted literally.

The prometheus registry is process-global and accumulates across tests, so
every counter assertion is a DELTA (before/after), never an absolute.
"""

from __future__ import annotations

from prometheus_client.parser import text_string_to_metric_families


def _sample(text: str, name: str, labels: dict | None = None) -> float | None:
    """Extract one sample value from Prometheus exposition text."""
    for family in text_string_to_metric_families(text):
        for s in family.samples:
            if s.name == name and (
                labels is None or all(s.labels.get(k) == v for k, v in labels.items())
            ):
                return s.value
    return None


def _scrape(authed_client) -> str:
    resp = authed_client.get("/metrics")
    assert resp.status_code == 200
    return resp.text


def _events_payload(n: int) -> dict:
    return {
        "events": [
            {
                "agent_name": f"metrics-test-agent-{i}",
                "model": "gpt-4o",
                "provider": "openai",
                "tokens_input": 100,
                "tokens_output": 50,
            }
            for i in range(n)
        ]
    }


def _disable_backlog_cache(monkeypatch) -> None:
    import app.metrics as metrics

    monkeypatch.setattr(metrics, "_BACKLOG_TTL_SECONDS", 0.0)


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_metrics_bad_key_401(authed_client):
    resp = authed_client.get(
        "/metrics", headers={"Authorization": "Bearer definitely-wrong"}
    )
    assert resp.status_code == 401


def test_metrics_missing_header_401(authed_client):
    resp = authed_client.get("/metrics", headers={"Authorization": ""})
    assert resp.status_code == 401


def test_metrics_pre_setup_503(client):
    """Documented: with no completed setup, a key-bearing scrape gets 503 —
    Prometheus shows the target down until the wizard runs."""
    resp = client.get("/metrics", headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 503


def test_metrics_scoped_token(client, monkeypatch):
    """CMA_METRICS_TOKEN grants /metrics WITHOUT the master API key — and
    works even pre-setup, so the scraper never needs ingest privileges."""
    from app.config import settings

    monkeypatch.setattr(settings, "METRICS_TOKEN", "scrape-secret")

    resp = client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert resp.status_code == 200

    resp = client.get("/metrics", headers={"Authorization": "Bearer wrong-secret"})
    assert resp.status_code == 503  # falls through to API-key auth: pre-setup


# ---------------------------------------------------------------------------
# Exposition + cardinality discipline
# ---------------------------------------------------------------------------


def test_exposition_contains_domain_metrics_without_user_labels(authed_client):
    """Every core family exists, and no cma_ sample carries agent_name or
    model. Samples are SEEDED first — an empty registry must not let the
    sweep pass vacuously."""
    from app.main import _run_maintenance_job

    authed_client.post("/api/v1/events", json=_events_payload(2))
    _run_maintenance_job("cardinality_probe", lambda: None)

    text = _scrape(authed_client)
    for family_name in (
        "cma_events_ingested",
        "cma_ingest_batch_size",
        "cma_budget_state_load_seconds",
        "cma_embedding_backlog_events",
        "cma_maintenance_last_success_timestamp_seconds",
        "cma_maintenance_failures",
    ):
        assert family_name in text, f"missing metric family: {family_name}"

    inspected = 0
    for family in text_string_to_metric_families(text):
        if not family.name.startswith("cma_"):
            continue
        for s in family.samples:
            inspected += 1
            assert "agent_name" not in s.labels, (
                f"{s.name} carries agent_name — forbidden cardinality"
            )
            assert "model" not in s.labels, (
                f"{s.name} carries model — forbidden cardinality"
            )
    assert inspected > 0, "cardinality sweep inspected nothing — vacuous"


def test_http_red_baseline_with_exclusions(authed_client):
    """The instrumentator emits handler-labeled HTTP metrics for API routes,
    and /metrics + /health are excluded (anchored patterns)."""
    authed_client.post("/api/v1/events", json=_events_payload(1))
    authed_client.get("/health")
    authed_client.get("/health/ready")
    text = _scrape(authed_client)  # also ensures /metrics itself was hit

    handlers = set()
    for family in text_string_to_metric_families(text):
        for s in family.samples:
            h = s.labels.get("handler")
            if h:
                handlers.add(h)

    assert "/api/v1/events" in handlers, (
        f"HTTP RED baseline missing events handler; saw: {sorted(handlers)}"
    )
    assert "/metrics" not in handlers, "excluded handler /metrics was instrumented"
    assert "/health" not in handlers, "excluded handler /health was instrumented"
    assert "/health/ready" not in handlers, (
        "excluded handler /health/ready was instrumented"
    )


# ---------------------------------------------------------------------------
# Ingestion metrics
# ---------------------------------------------------------------------------


def test_ingestion_increments_processed_batch_and_budget_timer(authed_client):
    text = _scrape(authed_client)
    processed_before = (
        _sample(text, "cma_events_ingested_total", {"status": "processed"}) or 0
    )
    count_before = _sample(text, "cma_ingest_batch_size_count") or 0
    sum_before = _sample(text, "cma_ingest_batch_size_sum") or 0
    budget_before = _sample(text, "cma_budget_state_load_seconds_count") or 0

    resp = authed_client.post("/api/v1/events", json=_events_payload(3))
    assert resp.status_code == 200
    assert resp.json()["processed"] == 3

    text = _scrape(authed_client)
    assert (
        _sample(text, "cma_events_ingested_total", {"status": "processed"})
        == processed_before + 3
    )
    assert (_sample(text, "cma_ingest_batch_size_count") or 0) == count_before + 1
    assert (_sample(text, "cma_ingest_batch_size_sum") or 0) == sum_before + 3
    assert (
        _sample(text, "cma_budget_state_load_seconds_count") or 0
    ) == budget_before + 1


def test_rejection_increments_rejected_counter(authed_client, monkeypatch):
    """Wiring test for the rejected path — budget logic itself is owned by
    test_budgets.py, so the rejection decision is stubbed."""
    import app.api.events as events_api

    monkeypatch.setattr(
        events_api,
        "event_rejection_reason",
        lambda *args, **kwargs: "budget stop (test)",
    )

    text = _scrape(authed_client)
    rejected_before = (
        _sample(text, "cma_events_ingested_total", {"status": "rejected"}) or 0
    )
    processed_before = (
        _sample(text, "cma_events_ingested_total", {"status": "processed"}) or 0
    )

    resp = authed_client.post("/api/v1/events", json=_events_payload(2))
    assert resp.status_code == 200
    assert resp.json()["rejected"] == 2

    text = _scrape(authed_client)
    assert (
        _sample(text, "cma_events_ingested_total", {"status": "rejected"})
        == rejected_before + 2
    )
    assert (
        _sample(text, "cma_events_ingested_total", {"status": "processed"}) or 0
    ) == processed_before


def test_savepoint_failure_counts_as_errored(authed_client, monkeypatch):
    """Events that die inside the per-event savepoint are the outcome that
    signals the SERVICE is broken — they must be visible, not lost between
    processed and rejected."""
    import app.api.events as events_api

    def _boom(event, db, cost):
        raise RuntimeError("processing exploded")

    monkeypatch.setattr(events_api, "_process_event", _boom)

    text = _scrape(authed_client)
    errored_before = (
        _sample(text, "cma_events_ingested_total", {"status": "errored"}) or 0
    )

    resp = authed_client.post("/api/v1/events", json=_events_payload(2))
    assert resp.status_code == 200
    assert resp.json()["processed"] == 0

    text = _scrape(authed_client)
    assert (
        _sample(text, "cma_events_ingested_total", {"status": "errored"})
        == errored_before + 2
    )


def test_413_batches_are_not_observed(authed_client, monkeypatch):
    """The batch-size histogram documents '413-rejected batches excluded' —
    an oversized batch must not move the histogram or the outcome counters."""
    from app.config import settings

    monkeypatch.setattr(settings, "MAX_EVENTS_PER_REQUEST", 3)

    text = _scrape(authed_client)
    count_before = _sample(text, "cma_ingest_batch_size_count") or 0
    processed_before = (
        _sample(text, "cma_events_ingested_total", {"status": "processed"}) or 0
    )

    resp = authed_client.post("/api/v1/events", json=_events_payload(4))
    assert resp.status_code == 413

    text = _scrape(authed_client)
    assert (_sample(text, "cma_ingest_batch_size_count") or 0) == count_before
    assert (
        _sample(text, "cma_events_ingested_total", {"status": "processed"}) or 0
    ) == processed_before


# ---------------------------------------------------------------------------
# Backlog gauge
# ---------------------------------------------------------------------------


def test_backlog_gauge_clamps_at_zero(authed_client, monkeypatch):
    """Orphaned vectors (best-effort deletes) can make the vector count
    exceed the SQL count — the gauge must clamp, not go negative."""
    import app.metrics as metrics

    _disable_backlog_cache(monkeypatch)
    monkeypatch.setattr(metrics, "_count_sql_events", lambda: 5)
    monkeypatch.setattr(metrics, "_count_vector_events", lambda: 10)
    text = _scrape(authed_client)
    assert _sample(text, "cma_embedding_backlog_events") == 0

    monkeypatch.setattr(metrics, "_count_sql_events", lambda: 10)
    monkeypatch.setattr(metrics, "_count_vector_events", lambda: 4)
    text = _scrape(authed_client)
    assert _sample(text, "cma_embedding_backlog_events") == 6


def test_scrape_survives_collector_failure(authed_client, monkeypatch):
    """A dead DB at scrape time must degrade to: gauge OMITTED (absent beats
    stale or fabricated-zero), scrape-error counter incremented, exposition
    still 200. Failures are never served from the TTL cache."""
    import app.metrics as metrics

    _disable_backlog_cache(monkeypatch)

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(metrics, "_count_sql_events", _boom)

    resp = authed_client.get("/metrics")
    assert resp.status_code == 200, "collector failure must not kill /metrics"
    assert _sample(resp.text, "cma_embedding_backlog_events") is None, (
        "gauge must be omitted on failure, not fabricated"
    )
    errors_first = _sample(resp.text, "cma_metrics_scrape_errors_total") or 0

    resp = authed_client.get("/metrics")
    assert resp.status_code == 200
    errors_second = _sample(resp.text, "cma_metrics_scrape_errors_total") or 0
    assert errors_second >= errors_first + 1, (
        "scrape errors must be counted on every failing scrape"
    )


# ---------------------------------------------------------------------------
# Maintenance jobs
# ---------------------------------------------------------------------------


def test_maintenance_job_names_are_the_published_contract():
    """Dashboards and alerts key on job="rollup" etc. — renaming or dropping
    a job must fail a test, not silently stale a panel."""
    from app.main import _maintenance_jobs
    from app.metrics import MAINTENANCE_JOB_NAMES

    names = [name for name, _ in _maintenance_jobs()]
    assert (
        names
        == list(MAINTENANCE_JOB_NAMES)
        == [
            "rollup",
            "retention",
            "embed_backfill",
        ]
    )


def test_maintenance_job_metrics():
    """Per-job success timestamps and failure counters via the runner
    helper. Failures are counted and swallowed — one broken job must not
    kill the maintenance loop."""
    from prometheus_client import REGISTRY

    from app.main import _run_maintenance_job

    _run_maintenance_job("test_job_ok", lambda: None)
    ts = REGISTRY.get_sample_value(
        "cma_maintenance_last_success_timestamp_seconds", {"job": "test_job_ok"}
    )
    assert ts is not None and ts > 0

    failures_before = (
        REGISTRY.get_sample_value(
            "cma_maintenance_failures_total", {"job": "test_job_fail"}
        )
        or 0
    )

    def _boom():
        raise RuntimeError("job exploded")

    _run_maintenance_job("test_job_fail", _boom)  # must not raise
    failures_after = REGISTRY.get_sample_value(
        "cma_maintenance_failures_total", {"job": "test_job_fail"}
    )
    assert failures_after == failures_before + 1
    assert (
        REGISTRY.get_sample_value(
            "cma_maintenance_last_success_timestamp_seconds",
            {"job": "test_job_fail"},
        )
        is None
    ), "a failing job must not report a success timestamp"


def test_failed_job_does_not_poison_later_jobs(monkeypatch):
    """Each job runs on its OWN session: a DB-level failure in one must not
    cascade PendingRollbackError into the next (one real failure reported
    as three), and pending rows from a half-finished job must never be
    committed by a later one. Regression test for the review finding."""
    from prometheus_client import REGISTRY

    import app.main as app_main

    def _db_death():
        raise RuntimeError("simulated DB failure mid-rollup")

    monkeypatch.setattr(app_main, "_rollup_job", _db_death)

    retention_failures_before = (
        REGISTRY.get_sample_value(
            "cma_maintenance_failures_total", {"job": "retention"}
        )
        or 0
    )

    for name, fn in app_main._maintenance_jobs():
        app_main._run_maintenance_job(name, fn)

    # rollup failed, but retention/embed_backfill ran independently — their
    # failure counters must NOT have moved.
    retention_failures_after = (
        REGISTRY.get_sample_value(
            "cma_maintenance_failures_total", {"job": "retention"}
        )
        or 0
    )
    assert retention_failures_after == retention_failures_before, (
        "rollup failure cascaded into retention — jobs share state"
    )
    assert (
        REGISTRY.get_sample_value(
            "cma_maintenance_last_success_timestamp_seconds", {"job": "retention"}
        )
        is not None
    ), "retention did not run after rollup failure"
