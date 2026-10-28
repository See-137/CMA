"""Auto-instrumentation for the OpenAI Python SDK (openai >= 1.0)."""

import logging
import time
import uuid
from typing import Any, AsyncIterator, Iterator

from cost_monitor._patching import (
    InstrumentedAsyncStream,
    InstrumentedStream,
    StreamState,
    config,
    aprecall_enforce,
    get_active_tracker,
    install,
    is_cancellation,
    precall_enforce,
)

logger = logging.getLogger("cost_monitor.auto.openai")

_warned_no_usage = False


def _warn_untracked_response() -> None:
    """A non-streaming response with no usage means the call bypassed
    instrumentation — with_raw_response/with_streaming_response surfaces
    return wrapper objects whose real content is parsed outside our code.
    Warn once so the blind spot is visible, debug-log thereafter."""
    global _warned_no_usage
    msg = (
        "cost_monitor: OpenAI response carried no usage — call NOT tracked. "
        "with_raw_response/with_streaming_response bypass instrumentation."
    )
    if _warned_no_usage:
        logger.debug(msg)
    else:
        _warned_no_usage = True
        logger.warning(msg)


def patch_openai() -> None:
    """Install idempotent patches over OpenAI chat completions (create and
    parse), legacy completions, embeddings and the Responses API (create and
    parse), sync and async.

    Safe to call repeatedly: already-patched methods are left untouched.
    The wrappers resolve the active tracker at call time, so re-running
    auto_instrument() swaps trackers without re-patching.
    """
    try:
        import openai  # noqa: F401
    except ImportError:
        logger.debug("openai package not installed, skipping patch")
        return

    _patch_chat_completions()
    _patch_legacy_completions()
    _patch_other_surfaces()


def _maybe_inject_usage(kwargs: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Ask OpenAI for the final usage-only chunk on streams — unless the
    integrator opted out (Azure api-versions may reject stream_options).

    Returns (kwargs, injected). ``injected`` is True only when CMA added
    include_usage itself: the usage-only chunk (``choices == []``) is then
    consumed by the wrapper and never yielded, because caller code written
    for the stream it asked for commonly indexes ``chunk.choices[0]``.
    """
    if not config.get("inject_stream_usage", True):
        return kwargs, False
    stream_options = dict(kwargs.get("stream_options") or {})
    if "include_usage" in stream_options:
        return kwargs, False
    stream_options["include_usage"] = True
    return {**kwargs, "stream_options": stream_options}, True


def _is_injected_usage_chunk(chunk: Any, hide: bool) -> bool:
    return hide and not getattr(chunk, "choices", None) and (
        getattr(chunk, "usage", None) is not None
    )


def _log_incomplete(tracker, model: str, trace_id: str, start: float) -> None:
    """The caller stopped reading before the provider reported usage. The
    call happened (input is billed) but its token counts are unknown."""
    tracker.log_event(
        model=model,
        provider="openai",
        tokens_input=0,
        tokens_output=0,
        duration_ms=int((time.time() - start) * 1000),
        status="incomplete",
        trace_id=trace_id,
        metadata={"reason": "stream closed by caller before usage was reported"},
    )


def _log_setup_exit(tracker, model, trace_id, start, exc: BaseException) -> None:
    """The request itself raised: an Exception is a failed call; task
    cancellation/Ctrl-C while waiting is an incomplete one."""
    if is_cancellation(exc):
        _log_incomplete(tracker, model, trace_id, start)
    elif isinstance(exc, Exception):
        _log_failure(tracker, model, trace_id, start, exc)


def _log_failure(
    tracker, model: str, trace_id: str, start: float, exc: BaseException
) -> None:
    tracker.log_event(
        model=model,
        provider="openai",
        tokens_input=0,
        tokens_output=0,
        duration_ms=int((time.time() - start) * 1000),
        status="failure",
        trace_id=trace_id,
        metadata={"error": str(exc)},
    )


def _usage_tokens(usage: Any) -> tuple[int, int]:
    """(input, output) from either usage shape: chat/completions/embeddings
    (prompt_tokens/completion_tokens) or Responses (input_tokens/output_tokens).
    """
    tokens_in = getattr(usage, "prompt_tokens", None)
    if tokens_in is None:
        tokens_in = getattr(usage, "input_tokens", 0)
    tokens_out = getattr(usage, "completion_tokens", None)
    if tokens_out is None:
        tokens_out = getattr(usage, "output_tokens", 0)
    return int(tokens_in or 0), int(tokens_out or 0)


def _chunk_usage(chunk: Any) -> Any:
    """Usage carried by a stream item: chat chunks have .usage; Responses
    terminal events (response.completed/incomplete/failed) carry
    .response.usage."""
    usage = getattr(chunk, "usage", None)
    if usage is None:
        usage = getattr(getattr(chunk, "response", None), "usage", None)
    return usage


# Responses API terminal status -> CMA event status. Billed tokens are kept
# either way; only the outcome differs.
_RESPONSE_STATUS = {"completed": "success", "incomplete": "incomplete", "failed": "failure"}
_TERMINAL_EVENTS = {
    "response.completed": "success",
    "response.incomplete": "incomplete",
    "response.failed": "failure",
}


def _outcome_status(obj: Any) -> str:
    """Status for a finished call from a stream event or a response object.
    Chat completions/embeddings carry no status and count as success."""
    event_status = _TERMINAL_EVENTS.get(getattr(obj, "type", None) or "")
    if event_status:
        return event_status
    status = getattr(obj, "status", None)
    return _RESPONSE_STATUS.get(status, "success") if isinstance(status, str) else "success"


def _log_usage(
    tracker,
    model: str,
    trace_id: str,
    start: float,
    usage: Any,
    status: str = "success",
) -> None:
    tokens_in, tokens_out = _usage_tokens(usage)
    tracker.log_event(
        model=model,
        provider="openai",
        tokens_input=tokens_in,
        tokens_output=tokens_out,
        duration_ms=int((time.time() - start) * 1000),
        status=status,
        trace_id=trace_id,
    )


def _wrap_sync_stream(
    stream: Any,
    tracker,
    model: str,
    trace_id: str,
    start: float,
    hide_usage_chunk: bool = False,
    state: StreamState | None = None,
) -> Iterator[Any]:
    """Yield every chunk unchanged (except a usage chunk CMA asked for);
    log usage after the final chunk."""
    usage = None
    status = "success"
    error: BaseException | None = None
    closed_early = False
    if state is not None:
        state.started = True
    try:
        for chunk in stream:
            chunk_usage = _chunk_usage(chunk)
            if chunk_usage is not None:
                usage = chunk_usage
                status = _outcome_status(chunk)
            if _is_injected_usage_chunk(chunk, hide_usage_chunk):
                continue
            yield chunk
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
            closed_early = True
            error = None
        if error is not None:
            _log_failure(tracker, model, trace_id, start, error)
        elif usage is not None:
            _log_usage(tracker, model, trace_id, start, usage, status)
        elif closed_early:
            _log_incomplete(tracker, model, trace_id, start)
        else:
            logger.warning(
                "cost_monitor: OpenAI stream ended with no usage chunk. "
                "Pass stream_options=dict(include_usage=True) or leave "
                "inject_stream_usage enabled."
            )


async def _wrap_async_stream(
    stream: Any,
    tracker,
    model: str,
    trace_id: str,
    start: float,
    hide_usage_chunk: bool = False,
    state: StreamState | None = None,
) -> AsyncIterator[Any]:
    """Async counterpart of _wrap_sync_stream."""
    usage = None
    status = "success"
    error: BaseException | None = None
    closed_early = False
    if state is not None:
        state.started = True
    try:
        async for chunk in stream:
            chunk_usage = _chunk_usage(chunk)
            if chunk_usage is not None:
                usage = chunk_usage
                status = _outcome_status(chunk)
            if _is_injected_usage_chunk(chunk, hide_usage_chunk):
                continue
            yield chunk
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
            closed_early = True
            error = None
        if error is not None:
            _log_failure(tracker, model, trace_id, start, error)
        elif usage is not None:
            _log_usage(tracker, model, trace_id, start, usage, status)
        elif closed_early:
            _log_incomplete(tracker, model, trace_id, start)
        else:
            logger.warning(
                "cost_monitor: OpenAI async stream ended with no usage chunk. "
                "Pass stream_options=dict(include_usage=True) or leave "
                "inject_stream_usage enabled."
            )


def _make_sync_wrapper(original_create, inject_usage: bool = True):
    def patched_create(self, *args, **kwargs):
        tracker = get_active_tracker()
        if tracker is None:
            return original_create(self, *args, **kwargs)
        precall_enforce(tracker, "openai", kwargs.get("model"))

        start = time.time()
        trace_id = str(uuid.uuid4())
        model = kwargs.get("model", "unknown")
        is_streaming = kwargs.get("stream", False)
        injected = False
        if is_streaming and inject_usage:
            kwargs, injected = _maybe_inject_usage(kwargs)

        try:
            response = original_create(self, *args, **kwargs)
        except BaseException as exc:
            _log_setup_exit(tracker, model, trace_id, start, exc)
            raise

        if is_streaming:
            state = StreamState(
                lambda: _log_incomplete(tracker, model, trace_id, start)
            )
            gen = _wrap_sync_stream(
                response, tracker, model, trace_id, start, injected, state
            )
            return InstrumentedStream(response, gen, state)

        usage = getattr(response, "usage", None)
        if usage:
            _log_usage(
                tracker, model, trace_id, start, usage, _outcome_status(response)
            )
        else:
            _warn_untracked_response()
        return response

    return patched_create


def _make_async_wrapper(original_acreate, inject_usage: bool = True):
    async def patched_acreate(self, *args, **kwargs):
        tracker = get_active_tracker()
        if tracker is None:
            return await original_acreate(self, *args, **kwargs)
        await aprecall_enforce(tracker, "openai", kwargs.get("model"))

        start = time.time()
        trace_id = str(uuid.uuid4())
        model = kwargs.get("model", "unknown")
        is_streaming = kwargs.get("stream", False)
        injected = False
        if is_streaming and inject_usage:
            kwargs, injected = _maybe_inject_usage(kwargs)

        try:
            response = await original_acreate(self, *args, **kwargs)
        except BaseException as exc:
            _log_setup_exit(tracker, model, trace_id, start, exc)
            raise

        if is_streaming:
            state = StreamState(
                lambda: _log_incomplete(tracker, model, trace_id, start)
            )
            gen = _wrap_async_stream(
                response, tracker, model, trace_id, start, injected, state
            )
            return InstrumentedAsyncStream(response, gen, state)

        usage = getattr(response, "usage", None)
        if usage:
            _log_usage(
                tracker, model, trace_id, start, usage, _outcome_status(response)
            )
        else:
            _warn_untracked_response()
        return response

    return patched_acreate


def _patch_chat_completions() -> None:
    from openai.resources.chat import completions as chat_mod

    if install(
        chat_mod.Completions,
        "create",
        _make_sync_wrapper(chat_mod.Completions.create),
        "openai.chat.sync",
    ):
        logger.debug("Patched openai.chat.completions.create")

    try:
        if install(
            chat_mod.AsyncCompletions,
            "create",
            _make_async_wrapper(chat_mod.AsyncCompletions.create),
            "openai.chat.async",
        ):
            logger.debug("Patched openai.chat.completions.AsyncCompletions.create")
    except AttributeError:
        pass


def _patch_legacy_completions() -> None:
    try:
        from openai.resources import completions as comp_mod

        if install(
            comp_mod.Completions,
            "create",
            _make_sync_wrapper(comp_mod.Completions.create),
            "openai.legacy.sync",
        ):
            logger.debug("Patched openai.completions.create")
    except (ImportError, AttributeError):
        pass


def _patch_other_surfaces() -> None:
    """Surfaces beyond chat.completions.create.

    parse() methods post directly instead of calling create(), so they need
    their own patch. The Responses API takes no stream_options.include_usage
    (usage arrives in the response.completed event), so nothing is injected.
    """
    targets = [
        # (module, class, method, usage injection allowed)
        ("openai.resources.chat.completions", "Completions", "parse", False),
        ("openai.resources.chat.completions", "AsyncCompletions", "parse", False),
        ("openai.resources.completions", "AsyncCompletions", "create", True),
        ("openai.resources.embeddings", "Embeddings", "create", False),
        ("openai.resources.embeddings", "AsyncEmbeddings", "create", False),
        ("openai.resources.responses", "Responses", "create", False),
        ("openai.resources.responses", "AsyncResponses", "create", False),
        ("openai.resources.responses", "Responses", "parse", False),
        ("openai.resources.responses", "AsyncResponses", "parse", False),
    ]
    import importlib

    for mod_name, cls_name, method, inject in targets:
        try:
            cls = getattr(importlib.import_module(mod_name), cls_name)
            original = getattr(cls, method)
        except (ImportError, AttributeError):
            continue
        factory = _make_async_wrapper if cls_name.startswith("Async") else _make_sync_wrapper
        key = f"{mod_name}.{cls_name}.{method}"
        if install(cls, method, factory(original, inject_usage=inject), key):
            logger.debug("Patched %s", key)
