"""Auto-instrumentation for the Anthropic Python SDK (anthropic >= 0.8)."""

import logging
import time
import uuid
from typing import Any, AsyncIterator, Iterator

from cost_monitor._patching import (
    InstrumentedAsyncStream,
    InstrumentedStream,
    get_active_tracker,
    install,
)

logger = logging.getLogger("cost_monitor.auto.anthropic")

_warned_no_usage = False


def _warn_untracked_response() -> None:
    """Non-streaming response without usage — the call bypassed
    instrumentation (raw/streaming-response wrapper surfaces). Warn once,
    debug-log thereafter."""
    global _warned_no_usage
    msg = (
        "cost_monitor: Anthropic response carried no usage — call NOT "
        "tracked. with_raw_response/with_streaming_response bypass "
        "instrumentation."
    )
    if _warned_no_usage:
        logger.debug(msg)
    else:
        _warned_no_usage = True
        logger.warning(msg)


def patch_anthropic() -> None:
    """Install idempotent patches over Messages.create and Messages.stream.

    Safe to call repeatedly: already-patched methods are left untouched.
    The wrappers resolve the active tracker at call time, so re-running
    auto_instrument() swaps trackers without re-patching.
    """
    try:
        import anthropic  # noqa: F401
    except ImportError:
        logger.debug("anthropic package not installed, skipping patch")
        return

    _patch_messages()
    _patch_messages_stream()


def _log_failure(
    tracker, model: str, trace_id: str, start: float, exc: BaseException
) -> None:
    tracker.log_event(
        model=model,
        provider="anthropic",
        tokens_input=0,
        tokens_output=0,
        duration_ms=int((time.time() - start) * 1000),
        status="failure",
        trace_id=trace_id,
        metadata={"error": str(exc)},
    )


def _log_tokens(
    tracker, model: str, trace_id: str, start: float, tokens_in: int, tokens_out: int
) -> None:
    tracker.log_event(
        model=model,
        provider="anthropic",
        tokens_input=tokens_in,
        tokens_output=tokens_out,
        duration_ms=int((time.time() - start) * 1000),
        status="success",
        trace_id=trace_id,
    )


def _wrap_sync_stream(
    stream: Any, tracker, model: str, trace_id: str, start: float
) -> Iterator[Any]:
    """Yield every event unchanged; accumulate usage from stream events.

    Anthropic sends input tokens in message_start and output tokens in the
    final message_delta.
    """
    tokens_in = 0
    tokens_out = 0
    error: BaseException | None = None
    try:
        for event in stream:
            etype = getattr(event, "type", None)
            if etype == "message_start":
                msg = getattr(event, "message", None)
                usage = getattr(msg, "usage", None) if msg else None
                tokens_in = getattr(usage, "input_tokens", 0) if usage else 0
            elif etype == "message_delta":
                usage = getattr(event, "usage", None)
                tokens_out = getattr(usage, "output_tokens", 0) if usage else 0
            yield event
    except BaseException as exc:
        error = exc
        raise
    finally:
        if error is not None:
            _log_failure(tracker, model, trace_id, start, error)
        elif tokens_in or tokens_out:
            _log_tokens(tracker, model, trace_id, start, tokens_in, tokens_out)
        else:
            logger.warning(
                "cost_monitor: Anthropic stream ended with no usage events. "
                "This may indicate an unsupported stream type."
            )


async def _wrap_async_stream(
    stream: Any, tracker, model: str, trace_id: str, start: float
) -> AsyncIterator[Any]:
    """Async counterpart of _wrap_sync_stream."""
    tokens_in = 0
    tokens_out = 0
    error: BaseException | None = None
    try:
        async for event in stream:
            etype = getattr(event, "type", None)
            if etype == "message_start":
                msg = getattr(event, "message", None)
                usage = getattr(msg, "usage", None) if msg else None
                tokens_in = getattr(usage, "input_tokens", 0) if usage else 0
            elif etype == "message_delta":
                usage = getattr(event, "usage", None)
                tokens_out = getattr(usage, "output_tokens", 0) if usage else 0
            yield event
    except BaseException as exc:
        error = exc
        raise
    finally:
        if error is not None:
            _log_failure(tracker, model, trace_id, start, error)
        elif tokens_in or tokens_out:
            _log_tokens(tracker, model, trace_id, start, tokens_in, tokens_out)
        else:
            logger.warning(
                "cost_monitor: Anthropic async stream ended with no usage "
                "events. This may indicate an unsupported stream type."
            )


class _InstrumentedStreamManager:
    """Delegating proxy over Anthropic's MessageStreamManager.

    __enter__ returns the SDK's own MessageStream untouched (full public
    API: text_stream, get_final_message, ...). Usage is read from
    current_message_snapshot on __exit__ — message_start populates input
    tokens, message_delta accumulates output tokens, so the snapshot is
    accurate for however much of the stream the caller consumed.
    """

    def __init__(self, manager: Any, tracker, model: str) -> None:
        self._cma_manager = manager
        self._cma_tracker = tracker
        self._cma_model = model
        self._cma_trace_id = str(uuid.uuid4())
        self._cma_start = time.time()
        self._cma_stream: Any = None
        self._cma_exited = False

    def __enter__(self) -> Any:
        # Python never calls __exit__ when __enter__ raises — a connection
        # or auth failure on session START is exactly the failure users
        # most need visibility into, so it must be logged here.
        try:
            self._cma_stream = self._cma_manager.__enter__()
        except Exception as exc:
            _log_failure(
                self._cma_tracker,
                self._cma_model,
                self._cma_trace_id,
                self._cma_start,
                exc,
            )
            raise
        return self._cma_stream

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        # Idempotency guard: a manual double-__exit__ must not log the same
        # usage twice. Our own tracking bugs are caught and logged rather
        # than swallowed (or worse, masking the user's exception).
        if not self._cma_exited:
            self._cma_exited = True
            try:
                if exc is not None:
                    _log_failure(
                        self._cma_tracker,
                        self._cma_model,
                        self._cma_trace_id,
                        self._cma_start,
                        exc,
                    )
                else:
                    snapshot = getattr(
                        self._cma_stream, "current_message_snapshot", None
                    )
                    usage = getattr(snapshot, "usage", None)
                    if usage is not None:
                        _log_tokens(
                            self._cma_tracker,
                            self._cma_model,
                            self._cma_trace_id,
                            self._cma_start,
                            getattr(usage, "input_tokens", 0) or 0,
                            getattr(usage, "output_tokens", 0) or 0,
                        )
                    else:
                        logger.warning(
                            "cost_monitor: messages.stream() ended without a "
                            "usage snapshot — SDK version may be unsupported."
                        )
            except Exception:
                logger.exception(
                    "cost_monitor: failed to record messages.stream() usage"
                )
        return self._cma_manager.__exit__(exc_type, exc, tb)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cma_manager, name)


def _patch_messages() -> None:
    from anthropic.resources import messages as msg_mod

    original_create = msg_mod.Messages.create

    def patched_create(self, *args, **kwargs):
        tracker = get_active_tracker()
        if tracker is None:
            return original_create(self, *args, **kwargs)

        start = time.time()
        trace_id = str(uuid.uuid4())
        model = kwargs.get("model", "unknown")
        is_streaming = kwargs.get("stream", False)

        try:
            response = original_create(self, *args, **kwargs)
        except Exception as exc:
            _log_failure(tracker, model, trace_id, start, exc)
            raise

        if is_streaming:
            gen = _wrap_sync_stream(response, tracker, model, trace_id, start)
            return InstrumentedStream(response, gen)

        usage = getattr(response, "usage", None)
        if usage:
            _log_tokens(
                tracker,
                model,
                trace_id,
                start,
                getattr(usage, "input_tokens", 0),
                getattr(usage, "output_tokens", 0),
            )
        else:
            _warn_untracked_response()
        return response

    if install(msg_mod.Messages, "create", patched_create, "anthropic.sync"):
        logger.debug("Patched anthropic.messages.create")

    try:
        original_acreate = msg_mod.AsyncMessages.create

        async def patched_acreate(self, *args, **kwargs):
            tracker = get_active_tracker()
            if tracker is None:
                return await original_acreate(self, *args, **kwargs)

            start = time.time()
            trace_id = str(uuid.uuid4())
            model = kwargs.get("model", "unknown")
            is_streaming = kwargs.get("stream", False)

            try:
                response = await original_acreate(self, *args, **kwargs)
            except Exception as exc:
                _log_failure(tracker, model, trace_id, start, exc)
                raise

            if is_streaming:
                gen = _wrap_async_stream(response, tracker, model, trace_id, start)
                return InstrumentedAsyncStream(response, gen)

            usage = getattr(response, "usage", None)
            if usage:
                _log_tokens(
                    tracker,
                    model,
                    trace_id,
                    start,
                    getattr(usage, "input_tokens", 0),
                    getattr(usage, "output_tokens", 0),
                )
            else:
                _warn_untracked_response()
            return response

        if install(msg_mod.AsyncMessages, "create", patched_acreate, "anthropic.async"):
            logger.debug("Patched anthropic.messages.AsyncMessages.create")
    except AttributeError:
        pass


def _patch_messages_stream() -> None:
    """Instrument Messages.stream — the SDK's recommended streaming API.

    It does NOT route through Messages.create, so the create patch never
    sees it. Returns a delegating manager whose __enter__ hands back the
    SDK's own MessageStream.
    """
    from anthropic.resources import messages as msg_mod

    original_stream = msg_mod.Messages.stream

    def patched_stream(self, *args, **kwargs):
        manager = original_stream(self, *args, **kwargs)
        tracker = get_active_tracker()
        if tracker is None:
            return manager
        return _InstrumentedStreamManager(
            manager, tracker, kwargs.get("model", "unknown")
        )

    if install(msg_mod.Messages, "stream", patched_stream, "anthropic.stream"):
        logger.debug("Patched anthropic.messages.stream")
