"""Shared fixtures for SDK transport tests.

Provides a scriptable fake CMA backend: a real threaded HTTP server whose
per-request behavior is driven by a script list, recording every request
body it receives. Windows-safe (stdlib http.server + threading only).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest


@dataclass
class RecordedRequest:
    path: str
    events: list[dict[str, Any]]
    status_sent: int
    client_received: bool = True


@dataclass
class FakeBackend:
    """Handle for a running scripted backend.

    script: list of directives consumed one per request, then default 200.
      - int: respond with that HTTP status
      - "hang:<seconds>": sleep, then respond 200
      - "validate": mimic the real backend's 422 contract — events with an
        empty model/provider get a FastAPI-style 422 detail with loc paths;
        valid payloads get 200.
    """

    server: ThreadingHTTPServer
    thread: threading.Thread
    script: list[Any] = field(default_factory=list)
    requests: list[RecordedRequest] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://127.0.0.1:{port}"

    def next_directive(self) -> Any:
        with self.lock:
            if self.script:
                return self.script.pop(0)
        return 200

    def record(
        self, path: str, events: list[dict], status: int, client_received: bool = True
    ) -> None:
        with self.lock:
            self.requests.append(RecordedRequest(path, events, status, client_received))

    def delivered_events(self) -> list[dict[str, Any]]:
        """Events from requests answered 200 AND actually received by the client.

        A hung request whose client timed out before the response was written
        does not count as delivered, even though the handler eventually sent 200.
        """
        with self.lock:
            return [
                ev
                for req in self.requests
                if req.status_sent == 200 and req.client_received
                for ev in req.events
            ]

    def requests_containing(self, agent_marker: str) -> list[RecordedRequest]:
        """All requests whose payload includes an event with this agent_name.

        Retry semantics are asserted on request count (a retried batch appears
        in more than one request) — server-side 200 status alone can't prove
        client receipt when the client timed out mid-request.
        """
        with self.lock:
            return [
                req
                for req in self.requests
                if any(ev.get("agent_name") == agent_marker for ev in req.events)
            ]

    def wait_for(self, predicate, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(self):
                return True
            time.sleep(0.02)
        return predicate(self)


def _extract_events(body: bytes) -> list[dict[str, Any]]:
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        return payload["events"]
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    return []


def _invalid_indices(events: list[dict[str, Any]]) -> list[tuple[int, str]]:
    bad: list[tuple[int, str]] = []
    for i, ev in enumerate(events):
        for field_name in ("model", "provider"):
            value = ev.get(field_name)
            if not isinstance(value, str) or len(value) < 1:
                bad.append((i, field_name))
    return bad


class _Handler(BaseHTTPRequestHandler):
    backend: FakeBackend  # injected per-server subclass

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        events = _extract_events(body)

        directive = self.backend.next_directive()
        status = 200
        response_body: dict[str, Any] = {"processed": len(events)}

        if isinstance(directive, str) and directive.startswith("hang:"):
            time.sleep(float(directive.split(":", 1)[1]))
        elif directive == "validate":
            bad = _invalid_indices(events)
            if bad:
                status = 422
                # Faithful to the real backend: the endpoint body is the union
                # EventsEnvelope | list[EventIn] | EventIn, and Pydantic v2
                # inserts the matched member's CLASS NAME into loc — the index
                # sits at loc[3], not loc[2]. The other union branches emit
                # decoy entries with no index at all; a loc parser must filter
                # for the "events"/<int>/<field> tail, not assume positions.
                detail = [
                    {
                        "type": "string_too_short",
                        "loc": ["body", "EventsEnvelope", "events", idx, field_name],
                        "msg": "String should have at least 1 character",
                    }
                    for idx, field_name in bad
                ]
                detail.append(
                    {
                        "type": "list_type",
                        "loc": ["body", "list[EventIn]"],
                        "msg": "Input should be a valid list",
                    }
                )
                detail.append(
                    {
                        "type": "missing",
                        "loc": ["body", "EventIn", "model"],
                        "msg": "Field required",
                    }
                )
                response_body = {"detail": detail}
        elif isinstance(directive, int):
            status = directive
            if status != 200:
                response_body = {"detail": f"scripted {status}"}

        payload = json.dumps(response_body).encode()
        client_received = True
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
        except (ConnectionError, OSError):
            client_received = False  # client gave up — never crash the server thread
        self.backend.record(self.path, events, status, client_received)

    def log_message(self, *args: Any) -> None:  # silence per-request stderr noise
        pass


@pytest.fixture
def fake_backend() -> FakeBackend:
    handler = type("BoundHandler", (_Handler,), {})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    backend = FakeBackend(server=server, thread=thread)
    handler.backend = backend
    thread.start()
    yield backend
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
