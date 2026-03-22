"""Auto-instrumentation for the Anthropic Python SDK (anthropic >= 0.7)."""

import time
import logging
import uuid
from cost_monitor.tracker import CostTracker

logger = logging.getLogger("cost_monitor.auto.anthropic")


def patch_anthropic(tracker: CostTracker) -> None:
    try:
        import anthropic
    except ImportError:
        logger.debug("anthropic package not installed, skipping patch")
        return

    _patch_messages(tracker)


def _patch_messages(tracker: CostTracker) -> None:
    from anthropic.resources import messages as msg_mod

    original_create = msg_mod.Messages.create

    def patched_create(self, *args, **kwargs):
        start = time.time()
        trace_id = str(uuid.uuid4())
        model = kwargs.get("model", "unknown")

        try:
            response = original_create(self, *args, **kwargs)
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
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            tracker.log_event(
                model=model,
                provider="anthropic",
                tokens_input=0,
                tokens_output=0,
                duration_ms=duration_ms,
                status="failure",
                trace_id=trace_id,
                metadata={"error": str(e)},
            )
            raise

    msg_mod.Messages.create = patched_create
    logger.debug("Patched anthropic.messages.create")

    # Async version
    try:
        original_acreate = msg_mod.AsyncMessages.create

        async def patched_acreate(self, *args, **kwargs):
            start = time.time()
            trace_id = str(uuid.uuid4())
            model = kwargs.get("model", "unknown")

            try:
                response = await original_acreate(self, *args, **kwargs)
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
            except Exception as e:
                duration_ms = int((time.time() - start) * 1000)
                tracker.log_event(
                    model=model,
                    provider="anthropic",
                    tokens_input=0,
                    tokens_output=0,
                    duration_ms=duration_ms,
                    status="failure",
                    trace_id=trace_id,
                    metadata={"error": str(e)},
                )
                raise

        msg_mod.AsyncMessages.create = patched_acreate
        logger.debug("Patched anthropic.messages.AsyncMessages.create")
    except AttributeError:
        pass
