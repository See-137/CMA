"""Auto-instrumentation for the OpenAI Python SDK (openai >= 1.0)."""

import time
import logging
import uuid
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


def _patch_chat_completions(tracker: CostTracker, openai_module) -> None:
    from openai.resources.chat import completions as chat_mod

    original_create = chat_mod.Completions.create

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
                    provider="openai",
                    tokens_input=getattr(usage, "prompt_tokens", 0),
                    tokens_output=getattr(usage, "completion_tokens", 0),
                    duration_ms=duration_ms,
                    status="success",
                    trace_id=trace_id,
                )
            return response
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            tracker.log_event(
                model=model,
                provider="openai",
                tokens_input=0,
                tokens_output=0,
                duration_ms=duration_ms,
                status="failure",
                trace_id=trace_id,
                metadata={"error": str(e)},
            )
            raise

    chat_mod.Completions.create = patched_create
    logger.debug("Patched openai.chat.completions.create")

    # Also patch async version if available
    try:
        original_acreate = chat_mod.AsyncCompletions.create

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
                        provider="openai",
                        tokens_input=getattr(usage, "prompt_tokens", 0),
                        tokens_output=getattr(usage, "completion_tokens", 0),
                        duration_ms=duration_ms,
                        status="success",
                        trace_id=trace_id,
                    )
                return response
            except Exception as e:
                duration_ms = int((time.time() - start) * 1000)
                tracker.log_event(
                    model=model,
                    provider="openai",
                    tokens_input=0,
                    tokens_output=0,
                    duration_ms=duration_ms,
                    status="failure",
                    trace_id=trace_id,
                    metadata={"error": str(e)},
                )
                raise

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

            try:
                response = original_create(self, *args, **kwargs)
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
            except Exception as e:
                duration_ms = int((time.time() - start) * 1000)
                tracker.log_event(
                    model=model,
                    provider="openai",
                    tokens_input=0,
                    tokens_output=0,
                    duration_ms=duration_ms,
                    status="failure",
                    trace_id=trace_id,
                    metadata={"error": str(e)},
                )
                raise

        comp_mod.Completions.create = patched_create
        logger.debug("Patched openai.completions.create")
    except (ImportError, AttributeError):
        pass
