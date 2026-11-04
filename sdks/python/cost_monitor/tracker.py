"""Core tracker implementation for CMA Python SDK."""

from __future__ import annotations

import atexit
import contextvars
import json
import logging
import math
import os
import random
import sys
import threading
import time
import uuid
import weakref
from collections import deque
from contextlib import contextmanager
from typing import Any, Generator

import httpx

from cost_monitor._usage import _int as _count
from cost_monitor._usage import usage_fields

logger = logging.getLogger("cost_monitor")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(
        logging.Formatter("[cost_monitor] %(levelname)s: %(message)s")
    )
    logger.addHandler(_handler)
    logger.setLevel(logging.WARNING)


# HTTP status codes that are transient; re-buffer the batch rather than drop.
# 413 is not here: the same bytes would fail forever, so _send splits instead.
_TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

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

# _send's answer to a 413: nothing is lost, retry smaller without waiting.
_SPLIT: list[dict[str, Any]] = []


# Attribution for calls in this context (thread/async task), read by every
# event and budget check: see attribution().
_scope: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "cma_attribution", default=None
)


@contextmanager
def attribution(
    agent: str | None = None,
    workflow: str | None = None,
    swarm: str | None = None,
    trace_id: str | None = None,
) -> Generator[None, None, None]:
    """Attribute every call inside the block, patched ones included.

    Example::

        with attribution(agent="researcher", workflow="nightly"):
            client.chat.completions.create(...)

    Works across threads only if the context is copied (as ``asyncio`` tasks
    and ``asyncio.to_thread`` do). An outer block's values apply where the
    inner one leaves a value unset. An explicit ``agent_name``, ``workflow`` or
    ``swarm`` argument to ``log_event`` wins over the block; the block's
    ``trace_id`` wins over the one patched calls generate for themselves.
    """
    merged = dict(_scope.get() or {})
    for key, value in (
        ("agent", agent),
        ("workflow", workflow),
        ("swarm", swarm),
        ("trace_id", trace_id),
    ):
        if value:
            merged[key] = value
    token = _scope.set(merged)
    try:
        yield
    finally:
        _scope.reset(token)


def _json_safe(value: Any, depth: int = 0) -> Any:
    """A copy the JSON encoder cannot choke on (datetimes, sets, NaN, objects).

    One unserialisable event used to fail its whole chunk (SDK-07). Copying
    also protects the buffered event from later mutation by the caller.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if depth > 20:
        return "<too deep>"
    if isinstance(value, dict):
        return {str(k): _json_safe(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v, depth + 1) for v in value]
    try:
        return str(value)
    except Exception:
        return "<unprintable>"


def _number(value: Any, integer: bool = False) -> Any:
    """A finite non-negative number, or None: the backend rejects the rest and
    a rejected field would 422 the whole batch."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number) if integer else number


# Trackers that still need a flush at exit or a reset after fork. Weak: a
# tracker dropped by the caller must stay collectable.
_live_trackers: weakref.WeakSet[CostTracker] = weakref.WeakSet()


def _close_live_trackers() -> None:
    for tracker in list(_live_trackers):
        try:
            tracker.close()
        except Exception:  # interpreter is going down: never raise from atexit
            logger.debug("close at exit failed", exc_info=True)


def _reset_after_fork() -> None:
    for tracker in list(_live_trackers):
        tracker._reset_in_child()


atexit.register(_close_live_trackers)
if hasattr(os, "register_at_fork"):  # not on Windows, where there is no fork
    os.register_at_fork(after_in_child=_reset_after_fork)


class BudgetExceeded(Exception):
    """Raised BEFORE an LLM call when a matching blocking budget is exhausted.

    ``violations`` holds the backend's description of each exhausted budget
    (name, scope, period, limit_amount, spent, message).
    """

    def __init__(self, violations: list[dict[str, Any]]) -> None:
        self.violations = violations
        message = "; ".join(str(v.get("message", "")) for v in violations)
        super().__init__(message or "CMA budget exhausted")


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
        tokens_cache_read: int = 0,
        tokens_cache_write: int = 0,
        tokens_cache_write_1h: int = 0,
    ) -> None:
        """Log a completion within this traced request.

        Duration is computed automatically from the trace start time.
        ``tokens_input`` counts all input tokens, cached ones included.
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
            tokens_cache_read=tokens_cache_read,
            tokens_cache_write=tokens_cache_write,
            tokens_cache_write_1h=tokens_cache_write_1h,
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
        budget_check_ttl: float = 10.0,
        budget_check_timeout: float = 2.0,
        budget_fail_closed: bool = False,
        close_timeout: float = 5.0,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._timeout = httpx.Timeout(timeout, connect=connect_timeout)
        self._close_timeout = close_timeout
        # Pre-call budget checks (check_budget / enforce_budget): results are
        # cached per attribution for budget_check_ttl seconds so enforcement
        # adds at most one short HTTP call per TTL to the caller's path.
        self._budget_ttl = budget_check_ttl
        self._budget_timeout = budget_check_timeout
        self._budget_fail_closed = budget_fail_closed
        self._unpriced_warned = False
        self._budget_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}
        self._budget_lock = threading.Lock()
        self._budget_key_locks: dict[tuple, threading.Lock] = {}
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
        self._send_options: dict[str, Any] = {}

        self._events_sent = 0
        self._events_dropped = 0
        self._events_rejected = 0
        self._events_duplicate = 0

        self._client = self._new_client()

        self._flush_thread: threading.Thread | None = None
        self._start_worker()
        _live_trackers.add(self)

    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
        )

    def _start_worker(self) -> None:
        # Daemon on purpose: a non-daemon worker paired with atexit-driven
        # close() deadlocks interpreter exit (CPython joins non-daemon threads
        # BEFORE atexit handlers run). close() is the explicit join point.
        self._flush_thread = threading.Thread(
            target=self._worker, daemon=True, name="cma-flush"
        )
        self._flush_thread.start()

    def _reset_in_child(self) -> None:
        """After fork() only the forking thread exists: the worker is gone, any
        lock may be held forever, and the client's sockets are shared with the
        parent, which still owns (and will send) everything buffered so far.
        The worker restarts on the child's first event: gunicorn and Celery
        children often fork again or never log."""
        self._lock = threading.Lock()
        self._drain_lock = threading.Lock()
        self._budget_lock = threading.Lock()
        self._budget_key_locks = {}
        self._wake = threading.Event()
        self._buffer.clear()
        self._pending = None
        self._flush_thread = None
        self._failures = 0
        self._retry_at = 0.0
        self._events_sent = 0
        self._events_dropped = 0
        self._events_rejected = 0
        self._events_duplicate = 0
        if not self._closed:
            self._client = self._new_client()

    @property
    def stats(self) -> dict[str, int]:
        """Delivery counters: buffered (waiting), sent (stored by the server),
        dropped (never reached the server), rejected (the server answered but
        did not store them) and duplicates (a retry of something already
        stored: not lost, not double counted)."""
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
                "rejected": self._events_rejected,
                "duplicates": self._events_duplicate,
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
        tokens_cache_read: int = 0,
        tokens_cache_write: int = 0,
        tokens_cache_write_1h: int = 0,
    ) -> None:
        """Log a single LLM cost event.

        ``tokens_input`` counts ALL input tokens. The optional cache counts
        say how many of them were read from / written to a prompt cache
        (``tokens_cache_write_1h``: the part of the writes with a 1-hour TTL);
        they are priced at the model's cache prices.

        The event is buffered and sent to the backend on the next flush.
        If the buffer reaches ``batch_size``, a flush is triggered immediately.
        Never raises: recording a cost must not break the call being recorded.
        """
        try:
            self._log_event(
                agent_name,
                model,
                provider,
                tokens_input,
                tokens_output,
                cost,
                duration_ms,
                status,
                workflow,
                swarm,
                trace_id,
                metadata,
                tokens_cache_read,
                tokens_cache_write,
                tokens_cache_write_1h,
            )
        except Exception:
            logger.warning("CMA could not record an event", exc_info=True)
            with self._lock:
                self._events_dropped += 1

    def _log_event(
        self,
        agent_name,
        model,
        provider,
        tokens_input,
        tokens_output,
        cost,
        duration_ms,
        status,
        workflow,
        swarm,
        trace_id,
        metadata,
        tokens_cache_read,
        tokens_cache_write,
        tokens_cache_write_1h,
    ) -> None:
        scope = _scope.get() or {}
        run = scope.get("_llm_run")
        if run is not None:
            run["recorded"] = True  # see _langchain: the handler stands down
        # Coerce to the backend schema before buffering: model/provider have
        # min_length=1 server-side, and one empty field would 422 the whole
        # envelope this event ships in.
        event: dict[str, Any] = {
            # Same id on every retry: a resend after a lost response is
            # recognised by the server instead of billed twice (ACC-10).
            "event_id": uuid.uuid4().hex,
            "timestamp": time.time(),
            "agent_name": _clip(
                agent_name
                or scope.get("agent")
                or self._default_agent
                or "unknown",
                _MAX_FIELD_LEN,
            ),
            "model": _clip(model, _MAX_FIELD_LEN) or "unknown",
            "provider": _clip(provider, _MAX_FIELD_LEN) or "unknown",
            # Coerced (None/"10"/10.0 -> int): an invalid count would 422 the
            # whole envelope this event ships in.
            "tokens_input": _count(tokens_input),
            "tokens_output": _count(tokens_output),
            "status": _clip(status, _MAX_STATUS_LEN) or "success",
            "workflow": _clip(
                workflow or scope.get("workflow") or self._default_workflow,
                _MAX_FIELD_LEN,
            ),
            "swarm": _clip(
                swarm or scope.get("swarm") or self._default_swarm, _MAX_FIELD_LEN
            ),
            "trace_id": _clip(scope.get("trace_id") or trace_id, _MAX_FIELD_LEN),
        }
        # Clamp so one inconsistent event cannot 422 the whole envelope:
        # the backend rejects cache counts larger than the input they are
        # part of. Sent only when non-zero (older backends ignore them).
        # Never raises: odd values (None, "10", 10.0) count as best they can.
        total = _count(tokens_input)
        cache_read = _count(tokens_cache_read)
        cache_write = _count(tokens_cache_write)
        if cache_read + cache_write > total:
            cache_read = min(cache_read, total)
            cache_write = total - cache_read
        cache_write_1h = min(_count(tokens_cache_write_1h), cache_write)
        for key, value in (
            ("tokens_cache_read", cache_read),
            ("tokens_cache_write", cache_write),
            ("tokens_cache_write_1h", cache_write_1h),
        ):
            if value:
                event[key] = value
        if cost is not None:
            clean_cost = _number(cost)
            if clean_cost is None:
                # Leave pricing to the server rather than lose the event.
                logger.warning("CMA ignored an invalid cost: %r", cost)
            else:
                event["cost"] = clean_cost
        clean_duration = _number(duration_ms, integer=True)
        if clean_duration is not None:
            event["duration_ms"] = clean_duration
        if metadata:
            event["metadata"] = _json_safe(metadata)

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
            if self._flush_thread is None:
                self._start_worker()  # first event in a forked child
        # Don't wake into an armed backoff window — sustained logging would
        # spin the worker wake->no-op until _retry_at passes. Unlocked read
        # is deliberate: worst case is one extra no-op wake on a stale value,
        # and the periodic flush_interval wake guarantees the retry anyway.
        if should_wake and time.monotonic() >= self._retry_at:
            self._wake.set()

    def check_budget(
        self,
        agent: str | None = None,
        workflow: str | None = None,
        swarm: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Ask the backend whether a new call with this attribution may proceed.

        Returns ``{"allowed": bool, "violations": [...]}``. Attribution falls
        back to the tracker defaults (agent ``"unknown"``, like log_event). If
        the backend is unreachable the result is allowed (fail-open,
        ``"error"`` set) unless the tracker was created with
        ``budget_fail_closed=True``. Never raises.
        """
        # Resolve attribution exactly as log_event() records it (fallbacks and
        # clipping), so the check sees the same agent/workflow/swarm the spend
        # accumulated under.
        key = self._budget_key(agent, workflow, swarm, provider, model)
        cached = self._cached_budget(key)
        if cached is not None:
            return cached

        # Single flight per attribution: on a cold or expired cache, one
        # caller asks the backend while concurrent callers wait for its answer.
        with self._budget_lock:
            key_lock = self._budget_key_locks.setdefault(key, threading.Lock())
        with key_lock:
            cached = self._cached_budget(key)
            if cached is not None:
                return cached
            result = self._fetch_budget(*key)
            with self._budget_lock:
                self._budget_cache[key] = (time.monotonic(), result)
            return result

    def _budget_key(
        self, agent, workflow, swarm, provider=None, model=None
    ) -> tuple:
        """Attribution resolved exactly as log_event() records it."""
        scope = _scope.get() or {}
        return (
            _clip(
                agent or scope.get("agent") or self._default_agent or "unknown",
                _MAX_FIELD_LEN,
            ),
            _clip(
                workflow or scope.get("workflow") or self._default_workflow,
                _MAX_FIELD_LEN,
            ),
            _clip(swarm or scope.get("swarm") or self._default_swarm, _MAX_FIELD_LEN),
            _clip(provider, _MAX_FIELD_LEN),
            _clip(model, _MAX_FIELD_LEN),
        )

    def _cached_budget(self, key: tuple) -> dict[str, Any] | None:
        """Unexpired cached result for a resolved attribution key, or None.
        Errors are cached briefly (1 s at most) so a blip doesn't pin a
        fail-closed tracker shut for a whole TTL."""
        now = time.monotonic()
        with self._budget_lock:
            entry = self._budget_cache.get(key)
        if entry is None:
            return None
        ttl = self._budget_ttl
        if "error" in entry[1]:
            ttl = min(ttl, 1.0)
        return entry[1] if now - entry[0] < ttl else None

    def _fetch_budget(self, agent, workflow, swarm, provider, model) -> dict[str, Any]:
        params = {
            k: v
            for k, v in (
                ("agent_name", agent),
                ("workflow", workflow),
                ("swarm", swarm),
                ("provider", provider),
                ("model", model),
            )
            if v
        }
        try:
            resp = self._client.get(
                "/api/v1/budgets/check", params=params, timeout=self._budget_timeout
            )
            resp.raise_for_status()
            body = resp.json()
            unpriced = body.get("unpriced_events")
            if unpriced and not self._unpriced_warned:
                logger.warning(
                    "CMA: %s events in this budget period have no model price; "
                    "the budget does not count them, so it may trip late",
                    unpriced,
                )
            # Warn again once the count has cleared and a new model appears.
            self._unpriced_warned = bool(unpriced)
            return {
                "allowed": bool(body.get("allowed", True)),
                "violations": list(body.get("violations") or []),
            }
        except Exception as exc:
            logger.warning("CMA budget check failed: %s", exc)
            if self._budget_fail_closed:
                return {
                    "allowed": False,
                    "violations": [
                        {"message": "CMA budget check unavailable (fail-closed)"}
                    ],
                    "error": str(exc),
                }
            return {"allowed": True, "violations": [], "error": str(exc)}

    def enforce_budget(
        self,
        agent: str | None = None,
        workflow: str | None = None,
        swarm: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        """Raise :class:`BudgetExceeded` if a matching blocking budget is
        exhausted. Call before an LLM call (auto_instrument(enforce_budgets=
        True) does this for every patched call)."""
        result = self.check_budget(
            agent=agent, workflow=workflow, swarm=swarm, provider=provider, model=model
        )
        if not result["allowed"]:
            raise BudgetExceeded(result["violations"])

    def enforce_budget_if_cached(
        self, provider: str | None = None, model: str | None = None
    ) -> bool:
        """Enforce from cache only (default attribution). Returns False when no
        fresh cached answer exists and a backend check is needed. Lets async
        callers skip a thread hop on the hot path."""
        cached = self._cached_budget(
            self._budget_key(None, None, None, provider, model)
        )
        if cached is None:
            return False
        if not cached["allowed"]:
            raise BudgetExceeded(cached["violations"])
        return True

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
        # Patched calls inside the block get its attribution too (SDK-10).
        with attribution(agent, workflow, swarm, resolved_trace_id):
            yield ctx

    def context(
        self,
        agent: str | None = None,
        workflow: str | None = None,
        swarm: str | None = None,
        trace_id: str | None = None,
    ):
        """Same as the module-level :func:`attribution`."""
        return attribution(agent, workflow, swarm, trace_id)

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
        Prompt-cache tokens are picked up from either shape (Anthropic cache
        reads/writes are added to the input total, as CMA expects).
        """
        fields = usage_fields(response_usage)
        for key, value in fields.items():
            kwargs.setdefault(key, value)
        self.log_event(
            agent_name=agent,
            model=model,
            provider=provider,
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
                # The chunk size can shrink while a chunk is parked (413).
                part, rest = chunk[: self._chunk_size], chunk[self._chunk_size :]
                remainder = self._send(part)
                if remainder is _SPLIT:
                    self._pending = chunk  # retried at once, in smaller parts
                elif remainder is None:
                    self._pending = rest or None
                    self._failures = 0
                    self._retry_at = 0.0
                else:
                    self._pending = remainder + rest
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
            response = self._client.post(
                "/api/v1/events", json={"events": chunk}, **self._send_options
            )
            response.raise_for_status()
            self._count_delivered(chunk, response)
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
            if status_code == 413:
                if len(chunk) > 1:
                    self._chunk_size = max(1, len(chunk) // 2)
                    logger.warning(
                        "CMA backend refused %d events as too large (HTTP 413) — "
                        "sending at most %d per request from now on.",
                        len(chunk),
                        self._chunk_size,
                    )
                    return _SPLIT
                logger.warning("One CMA event is too large (HTTP 413) — dropping it.")
                with self._lock:
                    self._events_dropped += 1
                return None
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

    def _count_delivered(
        self, chunk: list[dict[str, Any]], response: httpx.Response
    ) -> None:
        """A 200 does not mean stored: the body says what happened (SDK-11)."""
        try:
            body = response.json()
            rejected = int(body.get("rejected") or 0) + int(body.get("errored") or 0)
            duplicates = int(body.get("duplicates") or 0)
        except (ValueError, TypeError, AttributeError):
            rejected = duplicates = 0
        if rejected:
            logger.warning(
                "CMA backend did not store %d of %d events (see its log).",
                rejected,
                len(chunk),
            )
        with self._lock:  # all counter mutations live under _lock
            self._events_sent += max(len(chunk) - rejected - duplicates, 0)
            self._events_rejected += rejected
            self._events_duplicate += duplicates

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
        # The whole close has one time budget: an exit must not hang on a dead
        # backend. Requests made from here on are capped to what remains.
        deadline = time.monotonic() + self._close_timeout
        self._wake.set()
        worker = self._flush_thread
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=max(self._close_timeout / 2, 0.1))
            if worker.is_alive():
                logger.warning(
                    "CMA worker thread did not stop in time (backend hung?) — "
                    "draining on the caller thread anyway."
                )
        attempt = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if attempt:
                time.sleep(min(0.5, remaining))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
            attempt += 1
            left = max(remaining, 0.05)
            self._send_options = {
                "timeout": httpx.Timeout(
                    min(self._timeout.read or left, left),
                    connect=min(self._timeout.connect or left, left),
                )
            }
            # Bounded lock wait: a worker stuck mid-POST holds _drain_lock
            # for its HTTP timeout; close() must not inherit that unbounded.
            self._drain(ignore_backoff=True, lock_timeout=min(remaining, 2.0))
            with self._lock:
                if not self._buffer and not self._pending:
                    break
            if attempt >= 3:
                break
        # Drain exhausted — account for what we're abandoning. Shutdown
        # during an outage must not be an invisible loss path: every other
        # drop in this class is counted, so this one is too.
        abandoned = 0
        if self._drain_lock.acquire(timeout=0.5):
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
        _live_trackers.discard(self)
        self._client.close()

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> CostTracker:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
