"""Core tracker implementation for CMA Python SDK."""

from __future__ import annotations

import logging
import sys
import threading
import time
import uuid
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

    Thread-safe. Buffers events in memory and flushes them to the backend
    in batches — either when the buffer reaches ``batch_size`` or every
    ``flush_interval`` seconds, whichever comes first.

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
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._default_agent = default_agent
        self._default_workflow = default_workflow
        self._default_swarm = default_swarm
        self._flush_interval = flush_interval
        self._batch_size = batch_size

        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._running = True

        self._client = httpx.Client(
            base_url=self._endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="cma-flush"
        )
        self._flush_thread.start()

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
        event: dict[str, Any] = {
            "timestamp": time.time(),
            "agent_name": agent_name or self._default_agent or "unknown",
            "model": model,
            "provider": provider,
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "status": status,
            "workflow": workflow or self._default_workflow,
            "swarm": swarm or self._default_swarm,
            "trace_id": trace_id,
        }
        if cost is not None:
            event["cost"] = cost
        if duration_ms is not None:
            event["duration_ms"] = duration_ms
        if metadata:
            event["metadata"] = metadata

        needs_flush = False
        with self._lock:
            self._buffer.append(event)
            if len(self._buffer) >= self._batch_size:
                needs_flush = True

        if needs_flush:
            self.flush()

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

        Works with OpenAI-style usage objects that have ``prompt_tokens``
        and ``completion_tokens`` — whether they're dicts or attribute-bearing
        objects (e.g. ``response.usage``).
        """
        if isinstance(response_usage, dict):
            tokens_in = response_usage.get("prompt_tokens", 0)
            tokens_out = response_usage.get("completion_tokens", 0)
        else:
            tokens_in = getattr(response_usage, "prompt_tokens", 0)
            tokens_out = getattr(response_usage, "completion_tokens", 0)

        self.log_event(
            agent_name=agent,
            model=model,
            provider=provider,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            **kwargs,
        )

    def flush(self) -> None:
        """Send buffered events to the CMA backend.

        Swaps the buffer atomically so new events can be added while the
        HTTP request is in flight. Errors are logged to stderr — this method
        never raises.
        """
        with self._lock:
            if not self._buffer:
                return
            batch = self._buffer
            self._buffer = []

        try:
            self._client.post("/api/v1/events", json={"events": batch})
        except httpx.ConnectError:
            logger.warning(
                "Could not connect to CMA backend at %s — "
                "re-buffering %d events for next flush.",
                self._endpoint,
                len(batch),
            )
            with self._lock:
                self._buffer = batch + self._buffer
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "CMA backend returned HTTP %d — dropping %d events.",
                exc.response.status_code,
                len(batch),
            )
        except Exception:
            logger.warning(
                "Unexpected error flushing %d events to CMA backend.",
                len(batch),
                exc_info=True,
            )

    def close(self) -> None:
        """Flush remaining events and stop the background thread."""
        self._running = False
        self.flush()
        self._client.close()

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> CostTracker:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _flush_loop(self) -> None:
        """Background thread: flush the buffer every ``flush_interval`` seconds."""
        while self._running:
            time.sleep(self._flush_interval)
            try:
                self.flush()
            except Exception:
                # Belt-and-suspenders — flush() already catches everything,
                # but we never want this thread to die.
                pass
