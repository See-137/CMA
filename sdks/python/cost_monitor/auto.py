"""Zero-config auto-instrumentation for LLM libraries."""

import atexit
import logging
from cost_monitor.tracker import CostTracker

logger = logging.getLogger("cost_monitor.auto")

_tracker: CostTracker | None = None


def auto_instrument(
    endpoint: str,
    api_key: str,
    default_agent: str = "auto",
    flush_interval: float = 5.0,
) -> CostTracker:
    global _tracker
    if _tracker is not None:
        _tracker.close()

    _tracker = CostTracker(
        endpoint=endpoint,
        api_key=api_key,
        default_agent=default_agent,
        flush_interval=flush_interval,
    )

    # Patch OpenAI if installed
    try:
        from cost_monitor._openai import patch_openai

        patch_openai(_tracker)
        logger.info("OpenAI auto-instrumentation enabled")
    except ImportError:
        pass

    # Patch Anthropic if installed
    try:
        from cost_monitor._anthropic import patch_anthropic

        patch_anthropic(_tracker)
        logger.info("Anthropic auto-instrumentation enabled")
    except ImportError:
        pass

    atexit.register(stop)
    return _tracker


def stop():
    global _tracker
    if _tracker is not None:
        _tracker.close()
        _tracker = None


def get_tracker() -> CostTracker | None:
    return _tracker
