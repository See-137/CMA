"""Spec for structured JSON logging + request IDs (PR C).

Contract under test:
- app.logging_config.setup_logging() installs a JSON RotatingFileHandler
  writing to data/cma.log (the file /admin/logs reads — which today reads
  a file NOTHING writes) and creates data/ before the handler opens it.
- Log lines are one-JSON-object-per-line: timestamp, level, logger,
  message; request_id present when emitted inside a request context.
- A pure-ASGI middleware assigns each request an ID (honoring an incoming
  X-Request-ID after sanitization — log-injection via header must not be
  possible) and echoes it as a response header.
- /admin/logs returns real lines from the real file.

Console format stays human-readable in dev (CMA_LOG_JSON_CONSOLE flips it
for container deployments); these tests assert the FILE pipeline, which is
the machine-read surface.
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest


@pytest.fixture(scope="module", autouse=True)
def truncate_log_file():
    """Reset data/cma.log before this module runs.

    The file accumulates across local pytest sessions (append mode, once-
    per-process handler) — after enough runs it would cross the 10MB
    rotation threshold and the reversed-scan helpers would flake when a
    marker lands in a freshly rotated backup instead of the current file.
    Truncating with the handler's append handle open is safe same-process.
    """
    from app.logging_config import LOG_FILE

    try:
        open(LOG_FILE, "w").close()
    except OSError:
        pass
    yield


def _read_log_lines() -> list[str]:
    from app.logging_config import LOG_FILE

    with open(LOG_FILE, encoding="utf-8") as f:
        return [line for line in f.read().splitlines() if line.strip()]


def _emit_and_find(marker: str) -> dict:
    """Emit a log record and return its parsed JSON line from the file."""
    logging.getLogger("app.test_probe").info("probe %s", marker)
    for line in reversed(_read_log_lines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if marker in payload.get("message", ""):
            return payload
    raise AssertionError(f"marker {marker} not found in log file as JSON")


def test_file_log_lines_are_json_shaped():
    marker = uuid.uuid4().hex
    payload = _emit_and_find(marker)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test_probe"
    assert "timestamp" in payload
    assert payload["message"] == f"probe {marker}"


def test_exception_logs_carry_traceback():
    marker = uuid.uuid4().hex
    try:
        raise ValueError("boom for logging test")
    except ValueError:
        logging.getLogger("app.test_probe").exception("failed %s", marker)
    for line in reversed(_read_log_lines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if marker in payload.get("message", ""):
            assert "exc_info" in payload
            assert "ValueError" in payload["exc_info"]
            return
    raise AssertionError("exception log line not found")


def test_request_id_generated_and_echoed(client):
    resp = client.get("/health")
    rid = resp.headers.get("x-request-id")
    assert rid, "middleware must assign a request ID"
    assert len(rid) >= 8


def test_request_id_honored_when_supplied(client):
    resp = client.get("/health", headers={"X-Request-ID": "my-trace-42"})
    assert resp.headers.get("x-request-id") == "my-trace-42"


def test_request_id_header_is_sanitized(client):
    """A hostile X-Request-ID must not become a log-injection vector: control
    characters stripped, length clipped."""
    hostile = "abc\r\nInjected: line" + "x" * 200
    resp = client.get("/health", headers={"X-Request-ID": hostile})
    rid = resp.headers.get("x-request-id")
    assert rid
    assert "\r" not in rid and "\n" not in rid
    assert len(rid) <= 64


def test_contextvar_flows_into_json_lines():
    """The filter injects the request_id contextvar into every record."""
    from app.logging_config import request_id_var

    marker = uuid.uuid4().hex
    token = request_id_var.set("ctx-test-id")
    try:
        payload = _emit_and_find(marker)
    finally:
        request_id_var.reset(token)
    assert payload.get("request_id") == "ctx-test-id"


def test_in_request_logs_carry_the_request_id(authed_client, monkeypatch):
    """END-TO-END: a log line emitted while SERVING a request (here: the
    ingestion error path's logger.exception) carries the X-Request-ID the
    client sent — middleware sets the contextvar, filter injects it."""
    import app.api.events as events_api

    def _boom(event, db, cost):
        raise RuntimeError("forced for request-id logging test")

    monkeypatch.setattr(events_api, "_process_event", _boom)

    rid = f"req-{uuid.uuid4().hex[:12]}"
    resp = authed_client.post(
        "/api/v1/events",
        json={
            "events": [
                {
                    "agent_name": "log-test",
                    "model": "gpt-4o",
                    "provider": "openai",
                    "tokens_input": 10,
                    "tokens_output": 5,
                }
            ]
        },
        headers={"X-Request-ID": rid},
    )
    assert resp.status_code == 200  # savepoint isolates the failure

    for line in reversed(_read_log_lines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            payload.get("message") == "Failed to process event"
            and payload.get("request_id") == rid
        ):
            return
    raise AssertionError(f"no in-request log line carried request_id {rid}")


def test_background_task_logs_carry_the_request_id(authed_client, monkeypatch):
    """BackgroundTasks run via a direct await inside the response cycle —
    still within the middleware's contextvar scope — so their log lines
    must carry the originating request's ID. This is the docstring claim
    that is load-bearing for production correlation; pin it."""
    import app.api.events as events_api

    marker = uuid.uuid4().hex

    def _logging_check(db):
        logging.getLogger("app.test_probe").info("bg-task %s", marker)

    monkeypatch.setattr(events_api, "check_budgets", _logging_check)

    rid = f"bg-{uuid.uuid4().hex[:12]}"
    resp = authed_client.post(
        "/api/v1/events",
        json={
            "events": [
                {
                    "agent_name": "bg-log-test",
                    "model": "gpt-4o",
                    "provider": "openai",
                    "tokens_input": 10,
                    "tokens_output": 5,
                }
            ]
        },
        headers={"X-Request-ID": rid},
    )
    assert resp.status_code == 200

    for line in reversed(_read_log_lines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if marker in parsed.get("message", ""):
            assert parsed.get("request_id") == rid, (
                f"background-task log lost the request ID: {parsed}"
            )
            return
    raise AssertionError("background-task log line not found")


def test_admin_logs_returns_real_lines(authed_client):
    """/admin/logs must return the actual JSON lines being written — before
    this PR it read a file nothing wrote."""
    marker = uuid.uuid4().hex
    logging.getLogger("app.test_probe").info("admin-visible %s", marker)

    resp = authed_client.get(
        "/api/v1/admin/logs", headers={"X-Admin-Password": "testpass123"}
    )
    assert resp.status_code == 200
    lines = resp.json()["lines"]
    assert any(marker in line for line in lines), (
        "admin/logs did not return the freshly written line"
    )
