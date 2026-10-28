"""Auto-instrumentation for the Anthropic Python SDK (anthropic >= 0.8)."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, AsyncIterator, Iterator

from cost_monitor._patching import (
    InstrumentedAsyncStream,
    InstrumentedStream,
    StreamState,
    aprecall_enforce,
    get_active_tracker,
    install,
    is_cancellation,
    precall_enforce,
)
from cost_monitor._usage import cache_kwargs, merge_anthropic_delta, usage_fields

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
    tracker,
    model: str,
    trace_id: str,
    start: float,
    tokens_in: int,
    tokens_out: int,
    status: str = "success",
    metadata: dict | None = None,
    cache: dict | None = None,
) -> None:
    tracker.log_event(
        model=model,
        provider="anthropic",
        tokens_input=tokens_in,
        tokens_output=tokens_out,
        duration_ms=int((time.time() - start) * 1000),
        status=status,
        trace_id=trace_id,
        metadata=metadata,
        **(cache or {}),
    )


def _is_api_error(exc: BaseException) -> bool:
    """True for errors raised by the Anthropic client/API (a failed call);
    False for exceptions raised by the caller's own code."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover - patched only when installed
        return False
    return isinstance(exc, anthropic.AnthropicError)


def _log_unread(tracker, model, trace_id, start) -> None:
    _log_tokens(
        tracker, model, trace_id, start, 0, 0, status="incomplete",
        metadata={"reason": "stream closed before it was read"},
    )


def _log_setup_exit(tracker, model, trace_id, start, exc: BaseException) -> None:
    """The request itself raised: an Exception is a failed call; task
    cancellation/Ctrl-C while waiting is an incomplete one."""
    if is_cancellation(exc):
        _log_tokens(
            tracker, model, trace_id, start, 0, 0, status="incomplete",
            metadata={"reason": f"cancelled ({type(exc).__name__})"},
        )
    elif isinstance(exc, Exception):
        _log_failure(tracker, model, trace_id, start, exc)


_EARLY_CLOSE = {"reason": "stream closed by caller before the message completed"}


def _wrap_sync_stream(
    stream: Any, tracker, model: str, trace_id: str, start: float,
    state: StreamState | None = None,
) -> Iterator[Any]:
    """Yield every event unchanged; accumulate usage from stream events.

    Anthropic sends input tokens in message_start and output tokens in the
    final message_delta.
    """
    tokens_in = 0
    tokens_out = 0
    fields = usage_fields(None)
    cache: dict = {}
    error: BaseException | None = None
    closed_early = False
    finished = False
    if state is not None:
        state.started = True
    try:
        for event in stream:
            etype = getattr(event, "type", None)
            if etype == "message_start":
                msg = getattr(event, "message", None)
                fields = usage_fields(getattr(msg, "usage", None) if msg else None)
                tokens_in, cache = fields["tokens_input"], cache_kwargs(fields)
            elif etype == "message_delta":
                usage = getattr(event, "usage", None)
                tokens_out = getattr(usage, "output_tokens", 0) if usage else 0
                if usage is not None:
                    fields = merge_anthropic_delta(fields, usage)
                    tokens_in, cache = fields["tokens_input"], cache_kwargs(fields)
            elif etype == "message_stop":
                finished = True
            yield event
    except GeneratorExit:
        closed_early = True
        raise
    except BaseException as exc:
        error = exc
        raise
    finally:
        if error is not None and (
            is_cancellation(error) or (state is not None and state.closed_by_caller)
        ):
            closed_early, error = True, None
        if state is not None and state.closed_by_caller and not finished:
            # The caller's close may also end the read cleanly (EOF).
            closed_early = True
        if error is not None:
            # Input tokens from message_start are billed even when the
            # stream fails afterwards.
            _log_tokens(
                tracker,
                model,
                trace_id,
                start,
                tokens_in,
                tokens_out,
                status="failure",
                metadata={"error": str(error)[:500]},
                cache=cache,
            )
        elif closed_early:
            # Input tokens arrive in message_start, so an early stop still
            # knows (and is billed for) them.
            _log_tokens(
                tracker,
                model,
                trace_id,
                start,
                tokens_in,
                tokens_out,
                status="incomplete",
                metadata=_EARLY_CLOSE,
                cache=cache,
            )
        elif tokens_in or tokens_out:
            _log_tokens(
                tracker, model, trace_id, start, tokens_in, tokens_out, cache=cache
            )
        else:
            logger.warning(
                "cost_monitor: Anthropic stream ended with no usage events. "
                "This may indicate an unsupported stream type."
            )


async def _wrap_async_stream(
    stream: Any, tracker, model: str, trace_id: str, start: float,
    state: StreamState | None = None,
) -> AsyncIterator[Any]:
    """Async counterpart of _wrap_sync_stream."""
    tokens_in = 0
    tokens_out = 0
    fields = usage_fields(None)
    cache: dict = {}
    error: BaseException | None = None
    closed_early = False
    finished = False
    if state is not None:
        state.started = True
    try:
        async for event in stream:
            etype = getattr(event, "type", None)
            if etype == "message_start":
                msg = getattr(event, "message", None)
                fields = usage_fields(getattr(msg, "usage", None) if msg else None)
                tokens_in, cache = fields["tokens_input"], cache_kwargs(fields)
            elif etype == "message_delta":
                usage = getattr(event, "usage", None)
                tokens_out = getattr(usage, "output_tokens", 0) if usage else 0
                if usage is not None:
                    fields = merge_anthropic_delta(fields, usage)
                    tokens_in, cache = fields["tokens_input"], cache_kwargs(fields)
            elif etype == "message_stop":
                finished = True
            yield event
    except GeneratorExit:
        closed_early = True
        raise
    except BaseException as exc:
        error = exc
        raise
    finally:
        if error is not None and (
            is_cancellation(error) or (state is not None and state.closed_by_caller)
        ):
            closed_early, error = True, None
        if state is not None and state.closed_by_caller and not finished:
            # The caller's close may also end the read cleanly (EOF).
            closed_early = True
        if error is not None:
            # Input tokens from message_start are billed even when the
            # stream fails afterwards.
            _log_tokens(
                tracker,
                model,
                trace_id,
                start,
                tokens_in,
                tokens_out,
                status="failure",
                metadata={"error": str(error)[:500]},
                cache=cache,
            )
        elif closed_early:
            # Input tokens arrive in message_start, so an early stop still
            # knows (and is billed for) them.
            _log_tokens(
                tracker,
                model,
                trace_id,
                start,
                tokens_in,
                tokens_out,
                status="incomplete",
                metadata=_EARLY_CLOSE,
                cache=cache,
            )
        elif tokens_in or tokens_out:
            _log_tokens(
                tracker, model, trace_id, start, tokens_in, tokens_out, cache=cache
            )
        else:
            logger.warning(
                "cost_monitor: Anthropic async stream ended with no usage "
                "events. This may indicate an unsupported stream type."
            )


def _snapshot_or_none(stream: Any) -> Any:
    """The message accumulated so far, or None before message_start.

    MessageStream.current_message_snapshot *asserts* a snapshot exists, so a
    plain getattr raises AssertionError (not AttributeError) when the stream
    failed or the caller raised before the first event.
    """
    if stream is None:
        return None
    private = getattr(stream, "_current_snapshot_or_none", None)
    if callable(private):
        try:
            return private()
        except Exception:
            pass
    try:
        return stream.current_message_snapshot
    except (AssertionError, AttributeError):
        return None


def _record_stream_exit(tracker, model, trace_id, start, stream, exc) -> None:
    """Record a messages.stream() session from its final message snapshot.

    Snapshot tokens are always recorded (the API reported them, so they are
    billed). Status is 'failure' only for Anthropic client/API errors,
    'incomplete' when the caller's own code raised, else 'success'.
    """
    snapshot = _snapshot_or_none(stream)
    usage = getattr(snapshot, "usage", None)
    fields = usage_fields(usage)
    tokens = (fields["tokens_input"], fields["tokens_output"])
    cache = cache_kwargs(fields)
    args = (tracker, model, trace_id, start)
    if exc is not None and _is_api_error(exc):
        if usage is not None:
            _log_tokens(
                *args, *tokens, status="failure", metadata={"error": str(exc)[:500]},
                cache=cache,
            )
        else:
            _log_failure(*args, exc)
    elif usage is not None:
        if exc is not None:
            status, meta = "incomplete", {"reason": f"caller raised {type(exc).__name__}"}
        elif getattr(snapshot, "stop_reason", None) is None:
            # The caller left the block before the message finished.
            status, meta = "incomplete", _EARLY_CLOSE
        else:
            status, meta = "success", None
        _log_tokens(*args, *tokens, status=status, metadata=meta, cache=cache)
    elif exc is not None:
        # The caller's own code raised before message_start: the request was
        # sent but no tokens were reported yet.
        _log_tokens(
            *args, 0, 0, status="incomplete",
            metadata={"reason": f"caller raised {type(exc).__name__}"},
        )
    else:
        # Left the block before message_start: sent, nothing reported yet.
        _log_unread(*args)


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

    def _cma_args(self):
        return (self._cma_tracker, self._cma_model, self._cma_trace_id, self._cma_start)

    def __enter__(self) -> Any:
        # Python never calls __exit__ when __enter__ raises — a connection
        # or auth failure on session START is exactly the failure users
        # most need visibility into, so it must be logged here.
        try:
            self._cma_stream = self._cma_manager.__enter__()
        except BaseException as exc:
            _log_setup_exit(*self._cma_args(), exc)
            raise
        return self._cma_stream

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        # Idempotency guard: a manual double-__exit__ must not log the same
        # usage twice. Our own tracking bugs are caught and logged rather
        # than swallowed (or worse, masking the user's exception).
        if not self._cma_exited:
            self._cma_exited = True
            try:
                _record_stream_exit(*self._cma_args(), self._cma_stream, exc)
            except Exception:
                logger.exception(
                    "cost_monitor: failed to record messages.stream() usage"
                )
        return self._cma_manager.__exit__(exc_type, exc, tb)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cma_manager, name)


class _InstrumentedAsyncStreamManager:
    """Async counterpart: ``async with client.messages.stream(...)``.

    The SDK manager is created inside __aenter__, after the budget check, so
    a refused call leaves no un-awaited request coroutine behind. Only the
    async context-manager protocol is offered, as on the SDK's own manager.
    """

    def __init__(self, factory: Any, tracker, model: str) -> None:
        self._cma_factory = factory
        self._cma_manager: Any = None
        self._cma_tracker = tracker
        self._cma_model = model
        self._cma_trace_id = str(uuid.uuid4())
        self._cma_start = time.time()
        self._cma_stream: Any = None
        self._cma_exited = False

    def _cma_args(self):
        return (self._cma_tracker, self._cma_model, self._cma_trace_id, self._cma_start)

    async def __aenter__(self) -> Any:
        await aprecall_enforce(self._cma_tracker, "anthropic", self._cma_model)
        if self._cma_manager is not None:
            # The SDK's own manager cannot be entered twice either; without
            # this a second entry would send a second, unrecorded request.
            raise RuntimeError("messages.stream() manager cannot be reused")
        # Argument errors surface here, as from the SDK call itself: no
        # request was made, so nothing is recorded.
        self._cma_manager = self._cma_factory()
        self._cma_start = time.time()
        try:
            self._cma_stream = await self._cma_manager.__aenter__()
        except BaseException as exc:
            _log_setup_exit(*self._cma_args(), exc)
            raise
        return self._cma_stream

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        if not self._cma_exited:
            self._cma_exited = True
            try:
                _record_stream_exit(*self._cma_args(), self._cma_stream, exc)
            except Exception:
                logger.exception(
                    "cost_monitor: failed to record messages.stream() usage"
                )
        return await self._cma_manager.__aexit__(exc_type, exc, tb)


def _log_response_usage(tracker, model, trace_id, start, response) -> None:
    usage = getattr(response, "usage", None)
    if usage:
        fields = usage_fields(usage)
        _log_tokens(
            tracker,
            model,
            trace_id,
            start,
            fields["tokens_input"],
            fields["tokens_output"],
            cache=cache_kwargs(fields),
        )
    else:
        _warn_untracked_response()


def _make_create(original):
    """Wrapper for create()/parse(): non-streaming responses carry usage;
    stream=True returns an instrumented event stream."""

    def patched(self, *args, **kwargs):
        tracker = get_active_tracker()
        if tracker is None:
            return original(self, *args, **kwargs)
        precall_enforce(tracker, "anthropic", kwargs.get("model"))

        start = time.time()
        trace_id = str(uuid.uuid4())
        model = kwargs.get("model", "unknown")
        try:
            response = original(self, *args, **kwargs)
        except BaseException as exc:
            _log_setup_exit(tracker, model, trace_id, start, exc)
            raise
        if kwargs.get("stream", False):
            state = StreamState(lambda: _log_unread(tracker, model, trace_id, start))
            gen = _wrap_sync_stream(response, tracker, model, trace_id, start, state)
            return InstrumentedStream(response, gen, state)
        _log_response_usage(tracker, model, trace_id, start, response)
        return response

    return patched


def _make_acreate(original):
    async def patched(self, *args, **kwargs):
        tracker = get_active_tracker()
        if tracker is None:
            return await original(self, *args, **kwargs)
        await aprecall_enforce(tracker, "anthropic", kwargs.get("model"))

        start = time.time()
        trace_id = str(uuid.uuid4())
        model = kwargs.get("model", "unknown")
        try:
            response = await original(self, *args, **kwargs)
        except BaseException as exc:
            _log_setup_exit(tracker, model, trace_id, start, exc)
            raise
        if kwargs.get("stream", False):
            state = StreamState(lambda: _log_unread(tracker, model, trace_id, start))
            gen = _wrap_async_stream(response, tracker, model, trace_id, start, state)
            return InstrumentedAsyncStream(response, gen, state)
        _log_response_usage(tracker, model, trace_id, start, response)
        return response

    return patched


def _resource_modules() -> list[tuple[str, Any]]:
    """(label, module) for every Messages resource: stable and beta."""
    from anthropic.resources import messages as msg_mod

    mods = [("anthropic", msg_mod)]
    try:
        from anthropic.resources.beta.messages import messages as beta_mod

        mods.append(("anthropic.beta", beta_mod))
    except ImportError:
        pass
    return mods


def _patch_messages() -> None:
    for label, mod in _resource_modules():
        for cls_name, factory, suffix in (
            ("Messages", _make_create, "sync"),
            ("AsyncMessages", _make_acreate, "async"),
        ):
            cls = getattr(mod, cls_name, None)
            if cls is None:
                continue
            # parse() posts directly (it does not go through create()).
            for method in ("create", "parse"):
                original = getattr(cls, method, None)
                if original is None:
                    continue
                key = f"{label}.{method}.{suffix}"
                if install(cls, method, factory(original), key):
                    logger.debug("Patched %s", key)


def _patch_messages_stream() -> None:
    """Instrument Messages.stream / AsyncMessages.stream (stable and beta) —
    the SDK's recommended streaming API. It does NOT route through create(),
    so the create patch never sees it. Returns a delegating manager whose
    __enter__/__aenter__ hands back the SDK's own MessageStream.
    """
    for label, mod in _resource_modules():
        sync_cls = getattr(mod, "Messages", None)
        if sync_cls is not None and hasattr(sync_cls, "stream"):
            original_stream = sync_cls.stream

            def patched_stream(self, *args, _orig=original_stream, **kwargs):
                tracker = get_active_tracker()
                if tracker is not None:
                    precall_enforce(tracker, "anthropic", kwargs.get("model"))
                manager = _orig(self, *args, **kwargs)
                if tracker is None:
                    return manager
                return _InstrumentedStreamManager(
                    manager, tracker, kwargs.get("model", "unknown")
                )

            if install(sync_cls, "stream", patched_stream, f"{label}.stream.sync"):
                logger.debug("Patched %s.messages.stream", label)

        async_cls = getattr(mod, "AsyncMessages", None)
        if async_cls is not None and hasattr(async_cls, "stream"):
            original_astream = async_cls.stream

            # AsyncMessages.stream() is a plain method returning an async
            # context manager; enforcement runs in __aenter__ (it must await).
            def patched_astream(self, *args, _orig=original_astream, **kwargs):
                tracker = get_active_tracker()
                if tracker is None:
                    return _orig(self, *args, **kwargs)
                return _InstrumentedAsyncStreamManager(
                    lambda: _orig(self, *args, **kwargs),
                    tracker,
                    kwargs.get("model", "unknown"),
                )

            if install(async_cls, "stream", patched_astream, f"{label}.stream.async"):
                logger.debug("Patched %s.messages.AsyncMessages.stream", label)
