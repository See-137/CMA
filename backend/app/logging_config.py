"""Structured JSON logging with request-ID correlation.

Design decisions (from the observability review):
- stdlib dictConfig, NOT structlog: structlog's ergonomics only pay off
  after rewriting call sites across ~15 modules; a Formatter + Filter
  delivers JSON with request correlation for zero call-site changes.
- Console stays human-readable in dev (CMA_LOG_JSON_CONSOLE flips it to
  JSON for container deployments where stdout is scraped); the FILE is
  always JSON lines — it is the machine-read surface (/admin/logs, log
  shippers).
- request_id lives in a ContextVar set by RequestIDMiddleware; a Filter
  injects it into every record emitted on the request path (including
  BackgroundTasks, which run in the request's context).
- No uvicorn.access correlation is promised: uvicorn emits access lines
  outside the request context in common configurations. App loggers only.
- Windows caveat: RotatingFileHandler rotation is not multi-process safe;
  the dev launcher and Docker both run a single backend process. Same-
  process variant: /admin/logs holding a read handle at the exact moment
  rotation renames the file makes the rename fail (WinError 32) — logging
  swallows it and self-heals on the next emit; the reader retries once.
"""

from __future__ import annotations

import json
import logging
import logging.config
import os
import re
import uuid
from contextvars import ContextVar
from typing import Any

LOG_FILE = os.path.join("data", "cma.log")

request_id_var: ContextVar[str | None] = ContextVar("cma_request_id", default=None)

# Printable ASCII only. The load-bearing protection here is RESPONSE-HEADER
# injection: the ID is echoed verbatim into an X-Request-ID response header,
# so an unsanitized \r\n would be a CRLF/response-splitting vector. (Log
# injection is already neutralized by the JSON formatter's escaping —
# stripping here is defense in depth for that.)
_HEADER_SANITIZER = re.compile(r"[^\x20-\x7e]")
_MAX_REQUEST_ID_LEN = 64


def sanitize_request_id(raw: str) -> str:
    cleaned = _HEADER_SANITIZER.sub("", raw)[:_MAX_REQUEST_ID_LEN].strip()
    return cleaned or uuid.uuid4().hex[:16]


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, logger, message,
    request_id (when in a request context), exc_info (when present)."""

    def format(self, record: logging.LogRecord) -> str:
        from datetime import datetime, timezone

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class RequestIDMiddleware:
    """Pure-ASGI middleware: honor a sanitized incoming X-Request-ID or
    assign one, hold it in the contextvar for the request's lifetime, and
    echo it as a response header."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = ""
        for key, value in scope.get("headers") or []:
            if key == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        rid = sanitize_request_id(incoming) if incoming else uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", rid.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)


_configured = False


def setup_logging() -> None:
    """Install the console + JSON-file logging pipeline, once per process."""
    global _configured
    if _configured:
        return
    _configured = True

    # data/ must exist BEFORE the handler opens the file: lifespan's
    # makedirs runs later (and only on the SQLite branch). delay=True is
    # the second layer for exotic import orders.
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    from app.config import settings

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_context": {"()": RequestContextFilter},
            },
            "formatters": {
                "console": {
                    "format": "%(levelname)s:%(name)s:%(message)s",
                },
                "json": {"()": JsonFormatter},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json" if settings.LOG_JSON_CONSOLE else "console",
                    "filters": ["request_context"],
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "filename": LOG_FILE,
                    "maxBytes": 10 * 1024 * 1024,
                    "backupCount": 3,
                    "encoding": "utf-8",
                    "delay": True,
                    "formatter": "json",
                    "filters": ["request_context"],
                },
            },
            "root": {
                "level": settings.LOG_LEVEL,
                "handlers": ["console", "file"],
            },
        }
    )
