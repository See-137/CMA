"""Auto-instrumentation for the Anthropic Python SDK (anthropic >= 0.7)."""

import time
import logging
import uuid
from typing import Any, Iterator, AsyncIterator
from cost_monitor.tracker import CostTracker

logger = logging.getLogger("cost_monitor.auto.anthropic")


def patch_anthropic(tracker: CostTracker) -> None:
    try:
        import anthropic
    except ImportError:
        logger.debug("anthropic package not installed, skipping patch")
        return

    _patch_messages(tracker)



def _wrap_sync_stream(
    stream: Any,
    tracker,
    model: str,
    trace_id: str,
    start: float,
) -> Iterator[Any]:
    """Wrap a sync Anthropic stream; accumulate usage from stream events.

    Anthropic sends input token counts in message_start and output token
    counts in the final message_delta event.
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
        duration_ms = int((time.time() - start) * 1000)
        if error is not None:
            tracker.log_event(
                model=model,
                provider="anthropic",
                tokens_input=0,
                tokens_output=0,
                duration_ms=duration_ms,
                status="failure",
                trace_id=trace_id,
                metadata={"error": str(error)},
            )
        elif tokens_in or tokens_out:
            tracker.log_event(
                model=model,
                provider="anthropic",
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                duration_ms=duration_ms,
                status="success",
                trace_id=trace_id,
            )
        else:
            logger.warning(
                "cost_monitor: Anthropic stream ended with no usage events. "
                "This may indicate an unsupported stream type."
            )


async def _wrap_async_stream(
    stream: Any,
    tracker,
    model: str,
    trace_id: str,
    start: float,
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
        duration_ms = int((time.time() - start) * 1000)
        if error is not None:
            tracker.log_event(
                model=model,
                provider="anthropic",
                tokens_input=0,
                tokens_output=0,
                duration_ms=duration_ms,
                status="failure",
                trace_id=trace_id,
                metadata={"error": str(error)},
            )
        elif tokens_in or tokens_out:
            tracker.log_event(
                model=model,
                provider="anthropic",
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                duration_ms=duration_ms,
                status="success",
                trace_id=trace_id,
            )
        else:
            logger.warning(
                "cost_monitor: Anthropic async stream ended with no usage events. "
                "This may indicate an unsupported stream type."
            )

def _patch_messages(tracker: CostTracker) -> None:
    from anthropic.resources import messages as msg_mod

    original_create = msg_mod.Messages.create

    def patched_create(self, *args, **kwargs):
        start = time.time()
        trace_id = str(uuid.uuid4())
        model = kwargs.get("model", "unknown")
        is_streaming = kwargs.get("stream", False)

        try:
            response = original_create(self, *args, **kwargs)
        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            tracker.log_event(
                model=model,
                provider="anthropic",
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
                provider="anthropic",
                tokens_input=getattr(usage, "input_tokens", 0),
                tokens_output=getattr(usage, "output_tokens", 0),
                duration_ms=duration_ms,
                status="success",
                trace_id=trace_id,
            )
        return response

    msg_mod.Messages.create = patched_create
    logger.debug("Patched anthropic.messages.create")

    # Async version
    try:
        original_acreate = msg_mod.AsyncMessages.create

        async def patched_acreate(self, *args, **kwargs):
            start = time.time()
            trace_id = str(uuid.uuid4())
            model = kwargs.get("model", "unknown")
            is_streaming = kwargs.get("stream", False)

            try:
                response = await original_acreate(self, *args, **kwargs)
            except Exception as exc:
                duration_ms = int((time.time() - start) * 1000)
                tracker.log_event(
                    model=model,
                    provider="anthropic",
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
                    provider="anthropic",
                    tokens_input=getattr(usage, "input_tokens", 0),
                    tokens_output=getattr(usage, "output_tokens", 0),
                    duration_ms=duration_ms,
                    status="success",
                    trace_id=trace_id,
                )
            return response

        msg_mod.AsyncMessages.create = patched_acreate
        logger.debug("Patched anthropic.messages.AsyncMessages.create")
    except AttributeError:
        pass
