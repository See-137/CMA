"""Auto-instrumentation for the OpenAI Python SDK (openai >= 1.0)."""

import time
import logging
import uuid
from typing import Any, Iterator, AsyncIterator
from cost_monitor.tracker import CostTracker

logger = logging.getLogger("cost_monitor.auto.openai")


def patch_openai(tracker: CostTracker) -> None:
    try:
        import openai
    except ImportError:
        logger.debug("openai package not installed, skipping patch")
        return

    # Patch chat completions
    _patch_chat_completions(tracker, openai)
    # Patch completions (legacy)
    _patch_completions(tracker, openai)


def _wrap_sync_stream(
    stream: Any,
    tracker,
    model: str,
    trace_id: str,
    start: float,
) -> Iterator[Any]:
    """Wrap a sync OpenAI stream; emit a cost event after the last chunk.

    Requires stream_options=dict(include_usage=True) so OpenAI appends a
    final usage-only chunk. Every chunk is yielded unchanged.
    """
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
        duration_ms = int((time.time() - start) * 1000)
        if error is not None:
            tracker.log_event(
                model=model,
                provider="openai",
                tokens_input=0,
                tokens_output=0,
                duration_ms=duration_ms,
                status="failure",
                trace_id=trace_id,
                metadata={"error": str(error)},
            )
        elif usage is not None:
            tracker.log_event(
                model=model,
                provider="openai",
                tokens_input=getattr(usage, "prompt_tokens", 0),
                tokens_output=getattr(usage, "completion_tokens", 0),
                duration_ms=duration_ms,
                status="success",
                trace_id=trace_id,
            )
        else:
            logger.warning(
                "cost_monitor: OpenAI stream ended with no usage chunk. "
                "Pass stream_options=dict(include_usage=True) or upgrade openai>=1.26."
            )


async def _wrap_async_stream(
    stream: Any,
    tracker,
    model: str,
    trace_id: str,
    start: float,
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
        duration_ms = int((time.time() - start) * 1000)
        if error is not None:
            tracker.log_event(
                model=model,
                provider="openai",
                tokens_input=0,
                tokens_output=0,
                duration_ms=duration_ms,
                status="failure",
                trace_id=trace_id,
                metadata={"error": str(error)},
            )
        elif usage is not None:
            tracker.log_event(
                model=model,
                provider="openai",
                tokens_input=getattr(usage, "prompt_tokens", 0),
                tokens_output=getattr(usage, "completion_tokens", 0),
                duration_ms=duration_ms,
                status="success",
                trace_id=trace_id,
            )
        else:
            logger.warning(
                "cost_monitor: OpenAI async stream ended with no usage chunk. "
                "Pass stream_options=dict(include_usage=True) or upgrade openai>=1.26."
            )

def _patch_chat_completions(tracker: CostTracker, openai_module) -> None:
    from openai.resources.chat import completions as chat_mod

    original_create = chat_mod.Completions.create

    def patched_create(self, *args, **kwargs):
        start = time.time()
        trace_id = str(uuid.uuid4())
        model = kwargs.get("model", "unknown")
        is_streaming = kwargs.get("stream", False)

        if is_streaming:
            # Inject include_usage so OpenAI appends a final usage chunk.
            stream_options = dict(kwargs.get("stream_options") or {})
            stream_options.setdefault("include_usage", True)
            kwargs = {**kwargs, "stream_options": stream_options}

        try:
            response = original_create(self, *args, **kwargs)
        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            tracker.log_event(
                model=model,
                provider="openai",
                tokens_input=0,
                tokens_output=0,
                duration_ms=duration_ms,
                status="failure",
                trace_id=trace_id,
                metadata={"error": str(exc)},
            )
            raise

        if is_streaming:
            return _wrap_sync_stream(response, tracker, model, trace_id, start)

        duration_ms = int((time.time() - start) * 1000)
        usage = getattr(response, "usage", None)
        if usage:
            tracker.log_event(
                model=model,
                provider="openai",
                tokens_input=getattr(usage, "prompt_tokens", 0),
                tokens_output=getattr(usage, "completion_tokens", 0),
                duration_ms=duration_ms,
                status="success",
                trace_id=trace_id,
            )
        return response

    chat_mod.Completions.create = patched_create
    logger.debug("Patched openai.chat.completions.create")

    # Also patch async version if available
    try:
        original_acreate = chat_mod.AsyncCompletions.create

        async def patched_acreate(self, *args, **kwargs):
            start = time.time()
            trace_id = str(uuid.uuid4())
            model = kwargs.get("model", "unknown")
            is_streaming = kwargs.get("stream", False)

            if is_streaming:
                stream_options = dict(kwargs.get("stream_options") or {})
                stream_options.setdefault("include_usage", True)
                kwargs = {**kwargs, "stream_options": stream_options}

            try:
                response = await original_acreate(self, *args, **kwargs)
            except Exception as exc:
                duration_ms = int((time.time() - start) * 1000)
                tracker.log_event(
                    model=model,
                    provider="openai",
                    tokens_input=0,
                    tokens_output=0,
                    duration_ms=duration_ms,
                    status="failure",
                    trace_id=trace_id,
                    metadata={"error": str(exc)},
                )
                raise

            if is_streaming:
                return _wrap_async_stream(response, tracker, model, trace_id, start)

            duration_ms = int((time.time() - start) * 1000)
            usage = getattr(response, "usage", None)
            if usage:
                tracker.log_event(
                    model=model,
                    provider="openai",
                    tokens_input=getattr(usage, "prompt_tokens", 0),
                    tokens_output=getattr(usage, "completion_tokens", 0),
                    duration_ms=duration_ms,
                    status="success",
                    trace_id=trace_id,
                )
            return response

        chat_mod.AsyncCompletions.create = patched_acreate
        logger.debug("Patched openai.chat.completions.AsyncCompletions.create")
    except AttributeError:
        pass


def _patch_completions(tracker: CostTracker, openai_module) -> None:
    """Patch legacy completions endpoint."""
    try:
        from openai.resources import completions as comp_mod

        original_create = comp_mod.Completions.create

        def patched_create(self, *args, **kwargs):
            start = time.time()
            trace_id = str(uuid.uuid4())
            model = kwargs.get("model", "unknown")
            is_streaming = kwargs.get("stream", False)

            if is_streaming:
                stream_options = dict(kwargs.get("stream_options") or {})
                stream_options.setdefault("include_usage", True)
                kwargs = {**kwargs, "stream_options": stream_options}

            try:
                response = original_create(self, *args, **kwargs)
            except Exception as exc:
                duration_ms = int((time.time() - start) * 1000)
                tracker.log_event(
                    model=model,
                    provider="openai",
                    tokens_input=0,
                    tokens_output=0,
                    duration_ms=duration_ms,
                    status="failure",
                    trace_id=trace_id,
                    metadata={"error": str(exc)},
                )
                raise

            if is_streaming:
                return _wrap_sync_stream(response, tracker, model, trace_id, start)

            duration_ms = int((time.time() - start) * 1000)
            usage = getattr(response, "usage", None)
            if usage:
                tracker.log_event(
                    model=model,
                    provider="openai",
                    tokens_input=getattr(usage, "prompt_tokens", 0),
                    tokens_output=getattr(usage, "completion_tokens", 0),
                    duration_ms=duration_ms,
                    status="success",
                    trace_id=trace_id,
                )
            return response

        comp_mod.Completions.create = patched_create
        logger.debug("Patched openai.completions.create")
    except (ImportError, AttributeError):
        pass
