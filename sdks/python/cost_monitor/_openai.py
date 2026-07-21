"""Auto-instrumentation for the OpenAI Python SDK (openai >= 1.0)."""

import logging
import time
import uuid
from typing import Any, AsyncIterator, Iterator

from cost_monitor._patching import (
    InstrumentedAsyncStream,
    InstrumentedStream,
    config,
    get_active_tracker,
    install,
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
    """Install idempotent patches over OpenAI chat/legacy completions.

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


def _maybe_inject_usage(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Ask OpenAI for the final usage-only chunk on streams — unless the
    integrator opted out (Azure api-versions may reject stream_options)."""
    if not config.get("inject_stream_usage", True):
        return kwargs
    stream_options = dict(kwargs.get("stream_options") or {})
    stream_options.setdefault("include_usage", True)
    return {**kwargs, "stream_options": stream_options}


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


def _log_usage(tracker, model: str, trace_id: str, start: float, usage: Any) -> None:
    tracker.log_event(
        model=model,
        provider="openai",
        tokens_input=getattr(usage, "prompt_tokens", 0),
        tokens_output=getattr(usage, "completion_tokens", 0),
        duration_ms=int((time.time() - start) * 1000),
        status="success",
        trace_id=trace_id,
    )


def _wrap_sync_stream(
    stream: Any, tracker, model: str, trace_id: str, start: float
) -> Iterator[Any]:
    """Yield every chunk unchanged; log usage after the final chunk."""
    usage = None
    error: BaseException | None = None
    try:
        for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            yield chunk
    except BaseException as exc:
        error = exc
        raise
    finally:
        if error is not None:
            _log_failure(tracker, model, trace_id, start, error)
        elif usage is not None:
            _log_usage(tracker, model, trace_id, start, usage)
        else:
            logger.warning(
                "cost_monitor: OpenAI stream ended with no usage chunk. "
                "Pass stream_options=dict(include_usage=True) or leave "
                "inject_stream_usage enabled."
            )


async def _wrap_async_stream(
    stream: Any, tracker, model: str, trace_id: str, start: float
) -> AsyncIterator[Any]:
    """Async counterpart of _wrap_sync_stream."""
    usage = None
    error: BaseException | None = None
    try:
        async for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            yield chunk
    except BaseException as exc:
        error = exc
        raise
    finally:
        if error is not None:
            _log_failure(tracker, model, trace_id, start, error)
        elif usage is not None:
            _log_usage(tracker, model, trace_id, start, usage)
        else:
            logger.warning(
                "cost_monitor: OpenAI async stream ended with no usage chunk. "
                "Pass stream_options=dict(include_usage=True) or leave "
                "inject_stream_usage enabled."
            )


def _make_sync_wrapper(original_create):
    def patched_create(self, *args, **kwargs):
        tracker = get_active_tracker()
        if tracker is None:
            return original_create(self, *args, **kwargs)

        start = time.time()
        trace_id = str(uuid.uuid4())
        model = kwargs.get("model", "unknown")
        is_streaming = kwargs.get("stream", False)
        if is_streaming:
            kwargs = _maybe_inject_usage(kwargs)

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
            _log_usage(tracker, model, trace_id, start, usage)
        else:
            _warn_untracked_response()
        return response

    return patched_create


def _make_async_wrapper(original_acreate):
    async def patched_acreate(self, *args, **kwargs):
        tracker = get_active_tracker()
        if tracker is None:
            return await original_acreate(self, *args, **kwargs)

        start = time.time()
        trace_id = str(uuid.uuid4())
        model = kwargs.get("model", "unknown")
        is_streaming = kwargs.get("stream", False)
        if is_streaming:
            kwargs = _maybe_inject_usage(kwargs)

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
            _log_usage(tracker, model, trace_id, start, usage)
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
