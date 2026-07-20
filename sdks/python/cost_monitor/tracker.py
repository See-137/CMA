"""Core tracker implementation for CMA Python SDK."""

from __future__ import annotations

import logging
import random
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from typing import Any, Generator

import httpx

logger = logging.getLogger("cost_monitor")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(
        logging.Formatter("[cost_monitor] %(levelname)s: %(message)s")
    )
    logger.addHandler(_handler)
    logger.setLevel(logging.WARNING)


# HTTP status codes that are transient; re-buffer the batch rather than drop.
# 413 is included: the backend caps events-per-request, and a capped batch is
# retryable once draining is chunked — dropping it would destroy a backlog.
_TRANSIENT_STATUS_CODES = frozenset({408, 413, 429, 500, 502, 503, 504})

# Backend column limits (see backend/app/schemas/schemas.py) — clip client-side
# so one oversized field can't 422 an entire batch.
_MAX_FIELD_LEN = 255
_MAX_STATUS_LEN = 50

# Never exceed the backend's per-request cap (CMA_MAX_EVENTS_PER_REQUEST,
# default 1000) — one oversized POST would 413 and stall the whole backlog.
_MAX_CHUNK = 500

# Retry backoff: exponential with full jitter, capped. Base kept small so the
# first retry lands within a shutdown drain window.
_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 60.0


def _clip(value: str | None, limit: int) -> str | None:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


class RequestContext:
    """Context object yielded by CostTracker.trace_request.

    Captures timing automatically and provides a convenience method
    to log a completion event within the traced span.
    """

    def __init__(
        self,
        tracker: CostTracker,
        trace_id: str,
        agent: str | None,
        workflow: str | None,
        swarm: str | None,
        start_time: float,
    ) -> None:
        self.tracker = tracker
        self.trace_id = trace_id
        self.agent = agent
        self.workflow = workflow
        self.swarm = swarm
        self.start_time = start_time

    def log_completion(
        self,
        model: str,
        provider: str,
        tokens_input: int = 0,
        tokens_output: int = 0,
        cost: float | None = None,
        status: str = "success",
        metadata: dict | None = None,
    ) -> None:
        """Log a completion within this traced request.

        Duration is computed automatically from the trace start time.
        """
        duration_ms = int((time.time() - self.start_time) * 1000)
        self.tracker.log_event(
            agent_name=self.agent,
            model=model,
            provider=provider,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost=cost,
            duration_ms=duration_ms,
            status=status,
            workflow=self.workflow,
            swarm=self.swarm,
            trace_id=self.trace_id,
            metadata=metadata,
        )


class CostTracker:
    """Track LLM costs by sending events to a CMA backend instance.

    Thread-safe. Events go into a bounded drop-oldest buffer (``max_buffer_size``,
    losses counted in ``stats``); a single background worker performs all HTTP,
    draining in chunks of at most ``min(batch_size, 500)`` per request with
    capped exponential backoff on failure. Reaching ``batch_size`` wakes the
    worker early; ``log_event`` itself never touches the network.

    Use as a context manager for automatic cleanup::

        with CostTracker(endpoint="http://localhost:8000", api_key="key") as tracker:
            tracker.log_event(model="gpt-4o", provider="openai", tokens_input=100)
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        default_agent: str | None = None,
        default_workflow: str | None = None,
        default_swarm: str | None = None,
        flush_interval: float = 5.0,
        batch_size: int = 50,
        timeout: float = 10.0,
        connect_timeout: float = 5.0,
        max_buffer_size: int = 10_000,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._default_agent = default_agent
        self._default_workflow = default_workflow
        self._default_swarm = default_swarm
        self._flush_interval = flush_interval
        self._batch_size = batch_size
        self._chunk_size = max(1, min(batch_size, _MAX_CHUNK))

        # Bounded drop-OLDEST buffer: during an outage the freshest cost data
        # survives, memory stays capped, and every loss is counted.
        self._buffer: deque[dict[str, Any]] = deque(maxlen=max_buffer_size)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._running = True
        self._closed = False

        # In-flight chunk awaiting retry. Deliberately NOT re-queued into the
        # deque: extendleft on a full maxlen-deque evicts from the RIGHT —
        # newest events — silently inverting the drop-oldest policy.
        self._pending: list[dict[str, Any]] | None = None
        self._drain_lock = threading.Lock()
        self._failures = 0
        self._retry_at = 0.0

        self._events_sent = 0
        self._events_dropped = 0

        self._client = httpx.Client(
            base_url=self._endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
        )

        # Daemon on purpose: a non-daemon worker paired with atexit-driven
        # close() deadlocks interpreter exit (CPython joins non-daemon threads
        # BEFORE atexit handlers run). close() is the explicit join point.
        self._flush_thread = threading.Thread(
            target=self._worker, daemon=True, name="cma-flush"
        )
        self._flush_thread.start()

    @property
    def stats(self) -> dict[str, int]:
        """Delivery counters: buffered (waiting), sent, dropped (any cause)."""
        # _pending is only ever REPLACED (never mutated in place) under
        # _drain_lock, so an unlocked reference snapshot is coherent — taking
        # _drain_lock here would block stats for the length of an in-flight
        # POST. Counters are mutated under _lock, so read them there.
        pending = self._pending
        with self._lock:
            return {
                "buffered": len(self._buffer) + (len(pending) if pending else 0),
                "sent": self._events_sent,
                "dropped": self._events_dropped,
            }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_event(
        self,
        agent_name: str | None = None,
        model: str = "",
        provider: str = "",
        tokens_input: int = 0,
        tokens_output: int = 0,
        cost: float | None = None,
        duration_ms: int | None = None,
        status: str = "success",
        workflow: str | None = None,
        swarm: str | None = None,
        trace_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Log a single LLM cost event.

        The event is buffered and sent to the backend on the next flush.
        If the buffer reaches ``batch_size``, a flush is triggered immediately.
        """
        # Coerce to the backend schema before buffering: model/provider have
        # min_length=1 server-side, and one empty field would 422 the whole
        # envelope this event ships in.
        event: dict[str, Any] = {
            "timestamp": time.time(),
            "agent_name": _clip(
                agent_name or self._default_agent or "unknown", _MAX_FIELD_LEN
            ),
            "model": _clip(model, _MAX_FIELD_LEN) or "unknown",
            "provider": _clip(provider, _MAX_FIELD_LEN) or "unknown",
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "status": _clip(status, _MAX_STATUS_LEN) or "success",
            "workflow": _clip(workflow or self._default_workflow, _MAX_FIELD_LEN),
            "swarm": _clip(swarm or self._default_swarm, _MAX_FIELD_LEN),
            "trace_id": _clip(trace_id, _MAX_FIELD_LEN),
        }
        if cost is not None:
            event["cost"] = cost
        if duration_ms is not None:
            event["duration_ms"] = duration_ms
        if metadata:
            event["metadata"] = metadata

        # Signal-only: append and wake the worker. HTTP never runs on the
        # caller's thread — a slow backend must not stall the user's LLM path.
        should_wake = False
        with self._lock:
            # Running-flag read under _lock; close() flips it under the same
            # lock, so an append can never land after close()'s final drain
            # has already inspected the buffer.
            if not self._running:
                self._events_dropped += 1  # closed; a leaked patched function
                return  # must not resurrect the tracker
            if (
                self._buffer.maxlen is not None
                and len(self._buffer) >= self._buffer.maxlen
            ):
                self._events_dropped += 1  # deque evicts the OLDEST on append
                if self._events_dropped == 1 or self._events_dropped % 1000 == 0:
                    logger.warning(
                        "CMA buffer full (%d events) — dropped %d oldest "
                        "events so far. Backend down or ingestion too slow?",
                        self._buffer.maxlen,
                        self._events_dropped,
                    )
            self._buffer.append(event)
            if len(self._buffer) >= self._batch_size:
                should_wake = True
        # Don't wake into an armed backoff window — sustained logging would
        # spin the worker wake->no-op until _retry_at passes. Unlocked read
        # is deliberate: worst case is one extra no-op wake on a stale value,
        # and the periodic flush_interval wake guarantees the retry anyway.
        if should_wake and time.monotonic() >= self._retry_at:
            self._wake.set()

    @contextmanager
    def trace_request(
        self,
        agent: str | None = None,
        workflow: str | None = None,
        swarm: str | None = None,
        trace_id: str | None = None,
    ) -> Generator[RequestContext, None, None]:
        """Context manager that auto-tracks timing for an LLM call.

        Yields a :class:`RequestContext` whose ``log_completion`` method
        records duration automatically.

        Example::

            with tracker.trace_request(agent="summarizer") as ctx:
                response = call_llm(...)
                ctx.log_completion(model="gpt-4o", provider="openai",
                                   tokens_input=100, tokens_output=50)
        """
        resolved_trace_id = trace_id or uuid.uuid4().hex
        start = time.time()
        ctx = RequestContext(
            tracker=self,
            trace_id=resolved_trace_id,
            agent=agent or self._default_agent,
            workflow=workflow or self._default_workflow,
            swarm=swarm or self._default_swarm,
            start_time=start,
        )
        yield ctx

    def log_completion(
        self,
        response_usage: Any,
        model: str,
        provider: str,
        agent: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Convenience: log from an LLM response usage object.

        Accepts both OpenAI-style (``prompt_tokens`` / ``completion_tokens``)
        and Anthropic-style (``input_tokens`` / ``output_tokens``) usage
        objects, whether dicts or attribute-bearing objects (e.g. ``response.usage``).
        """
        if isinstance(response_usage, dict):
            tokens_in = (
                response_usage.get("prompt_tokens")
                or response_usage.get("input_tokens")
                or 0
            )
            tokens_out = (
                response_usage.get("completion_tokens")
                or response_usage.get("output_tokens")
                or 0
            )
        else:
            tokens_in = (
                getattr(response_usage, "prompt_tokens", None)
                or getattr(response_usage, "input_tokens", None)
                or 0
            )
            tokens_out = (
                getattr(response_usage, "completion_tokens", None)
                or getattr(response_usage, "output_tokens", None)
                or 0
            )

        self.log_event(
            agent_name=agent,
            model=model,
            provider=provider,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            **kwargs,
        )

    def flush(self) -> None:
        """Drain the buffer now, best-effort, on the calling thread.

        Bypasses the backoff gate. Never raises. Normal delivery happens on
        the background worker — call this only when you need an immediate
        synchronous drain (e.g. right before process exit without close()).
        """
        self._drain(ignore_backoff=True)

    # ------------------------------------------------------------------
    # Worker + drain pipeline
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        """Background loop: the ONLY code path that performs HTTP in steady
        state. Wakes on the flush timer or a batch_size signal; a wake that
        lands inside a backoff window is a cheap no-op in _drain."""
        while self._running:
            self._wake.wait(timeout=self._flush_interval)
            self._wake.clear()
            if not self._running:
                break
            self._drain()

    def _drain(
        self, ignore_backoff: bool = False, lock_timeout: float | None = None
    ) -> None:
        """Send pending + buffered events in chunks until empty or failure.

        On failure the chunk is parked in self._pending and the backoff
        window is armed; producers keep appending (bounded) meanwhile.
        lock_timeout bounds the wait for a worker stuck mid-POST — used by
        close() so its shutdown budget stays real.
        """
        acquired = self._drain_lock.acquire(
            timeout=-1 if lock_timeout is None else lock_timeout
        )
        if not acquired:
            return
        try:
            if not ignore_backoff and time.monotonic() < self._retry_at:
                return
            while True:
                chunk = self._pending or self._pop_chunk()
                if not chunk:
                    self._pending = None
                    return
                remainder = self._send(chunk)
                if remainder is None:
                    self._pending = None
                    self._failures = 0
                    self._retry_at = 0.0
                else:
                    self._pending = remainder
                    self._failures += 1
                    delay = min(
                        _BACKOFF_CAP,
                        _BACKOFF_BASE * (2 ** min(self._failures - 1, 10)),
                    )
                    self._retry_at = time.monotonic() + delay * random.uniform(0.5, 1.0)
                    return
        finally:
            self._drain_lock.release()

    def _pop_chunk(self) -> list[dict[str, Any]]:
        with self._lock:
            n = min(len(self._buffer), self._chunk_size)
            return [self._buffer.popleft() for _ in range(n)]

    def _send(self, chunk: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """POST one chunk. Returns None when the chunk is finished (delivered
        or permanently dropped), or the events to retry after backoff."""
        try:
            response = self._client.post("/api/v1/events", json={"events": chunk})
            response.raise_for_status()
            with self._lock:  # all counter mutations live under _lock
                self._events_sent += len(chunk)
            return None
        except httpx.TransportError:
            # The whole network-failure family — ConnectError, ConnectTimeout,
            # ReadTimeout, ReadError, RemoteProtocolError — is retryable.
            # (httpx does not raise on HTTP status; without raise_for_status
            # above, the HTTPStatusError branch below would be unreachable.)
            logger.warning(
                "Network error reaching CMA backend at %s — will retry "
                "%d events with backoff.",
                self._endpoint,
                len(chunk),
            )
            return chunk
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in _TRANSIENT_STATUS_CODES:
                logger.warning(
                    "CMA backend returned transient HTTP %d — will retry "
                    "%d events with backoff.",
                    status_code,
                    len(chunk),
                )
                return chunk
            if status_code == 422:
                return self._surviving_events(chunk, exc.response)
            logger.warning(
                "CMA backend returned HTTP %d — dropping %d events.",
                status_code,
                len(chunk),
            )
            with self._lock:
                self._events_dropped += len(chunk)
            return None
        except Exception:
            # Truly unexpected (serialization bugs etc.) — dropping is
            # deliberate: retrying an unsendable chunk would loop forever.
            logger.error(
                "Unexpected error sending %d events to CMA backend — dropping.",
                len(chunk),
                exc_info=True,
            )
            with self._lock:
                self._events_dropped += len(chunk)
            return None

    def _surviving_events(
        self, chunk: list[dict[str, Any]], response: httpx.Response
    ) -> list[dict[str, Any]] | None:
        """On 422, drop only the schema-invalid events; return the rest.

        FastAPI reports per-event indices in detail[].loc, but the ingestion
        body is a union (EventsEnvelope | list[EventIn] | EventIn), so loc
        paths carry the matched member's class name and the other union
        branches emit decoy entries with no index at all. Scan each loc for
        the "events", <int> tail instead of assuming positions.
        """
        poisoned: set[int] = set()
        try:
            detail = response.json().get("detail", [])
        except Exception:
            detail = []
        for entry in detail:
            loc = entry.get("loc", []) if isinstance(entry, dict) else []
            for i in range(len(loc) - 1):
                if loc[i] == "events" and isinstance(loc[i + 1], int):
                    poisoned.add(loc[i + 1])
                    break

        if not poisoned:
            logger.error(
                "CMA backend rejected a %d-event batch (422) without per-event "
                "indices — dropping the batch. Response: %.500s",
                len(chunk),
                response.text,
            )
            with self._lock:
                self._events_dropped += len(chunk)
            return None

        for idx in sorted(poisoned):
            if 0 <= idx < len(chunk):
                logger.error(
                    "Dropping schema-invalid event rejected by CMA backend: %r",
                    chunk[idx],
                )
        with self._lock:
            self._events_dropped += len(poisoned & set(range(len(chunk))))
        survivors = [ev for i, ev in enumerate(chunk) if i not in poisoned]
        return survivors or None

    def close(self) -> None:
        """Stop and join the worker, then drain remaining events.

        Sequence: stop flag -> wake -> join(timeout) -> bounded final drain.
        The drain runs after the join so it never races the worker; a
        transient failure on the final drain gets a short retry window —
        without it, one 500 at shutdown silently strands the whole buffer.
        Idempotent: safe to call more than once (atexit + explicit close).
        """
        with self._lock:
            # Flag flips under _lock: log_event checks _running under the
            # same lock, so no append can slip in after the drain below has
            # inspected the buffer, and concurrent close() calls can't both
            # pass the idempotency guard.
            if self._closed:
                return
            self._closed = True
            self._running = False
        self._wake.set()
        self._flush_thread.join(timeout=self._flush_interval + 15.0)
        if self._flush_thread.is_alive():
            logger.warning(
                "CMA worker thread did not stop within its join timeout — "
                "draining on the caller thread anyway."
            )
        for attempt in range(3):
            if attempt:
                time.sleep(0.5)
            # Bounded lock wait: a worker stuck mid-POST holds _drain_lock
            # for its HTTP timeout; close() must not inherit that unbounded.
            self._drain(ignore_backoff=True, lock_timeout=2.0)
            with self._lock:
                if not self._buffer and not self._pending:
                    break
        # Drain exhausted — account for what we're abandoning. Shutdown
        # during an outage must not be an invisible loss path: every other
        # drop in this class is counted, so this one is too.
        abandoned = 0
        if self._drain_lock.acquire(timeout=2.0):
            try:
                abandoned += len(self._pending or [])
                self._pending = None
            finally:
                self._drain_lock.release()
        with self._lock:
            abandoned += len(self._buffer)
            self._buffer.clear()
            if abandoned:
                self._events_dropped += abandoned
        if abandoned:
            logger.warning(
                "Dropping %d undeliverable events at close() — backend "
                "unreachable through the shutdown drain window.",
                abandoned,
            )
        self._client.close()

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> CostTracker:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
