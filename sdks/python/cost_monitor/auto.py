"""Zero-config auto-instrumentation for LLM libraries."""

import atexit
import logging
from cost_monitor.tracker import CostTracker

logger = logging.getLogger("cost_monitor.auto")

_tracker: CostTracker | None = None
_atexit_registered = False  # guard against stacking atexit callbacks on repeated calls


def auto_instrument(
    endpoint: str,
    api_key: str,
    default_agent: str = "auto",
    flush_interval: float = 5.0,
) -> CostTracker:
    """Instrument all installed LLM libraries (OpenAI, Anthropic, LangChain).

    Safe to call multiple times; the previous tracker is closed and replaced.

    LangChain note: this function registers a :class:`CMACallbackHandler` in
    LangChain's global callback manager when langchain_core (or langchain) is
    installed. If you need per-chain control, import and attach the handler
    manually instead::

        from cost_monitor._langchain import CMACallbackHandler
        llm = ChatOpenAI(callbacks=[CMACallbackHandler(tracker)])
    """
    global _tracker, _atexit_registered

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

    # Register LangChain global callback if langchain_core or langchain is installed.
    # We attempt to inject into the global callback manager so all chains are covered
    # automatically. If the global manager is unavailable (older langchain version or
    # incompatible API), we log a one-time warning and skip silently.
    try:
        from cost_monitor._langchain import CMACallbackHandler

        handler = CMACallbackHandler(_tracker)

        try:
            # langchain_core >= 0.1 exposes a global callback manager
            from langchain_core.callbacks import get_callback_manager  # type: ignore[attr-defined]

            get_callback_manager().add_handler(handler, inherit=True)
            logger.info("LangChain auto-instrumentation enabled (global callback manager)")
        except (ImportError, AttributeError):
            # Global manager not available in this version; attach to BaseCallbackManager
            # if possible, otherwise advise manual attachment.
            try:
                from langchain.callbacks import get_callback_manager as _gcm  # type: ignore[attr-defined]

                _gcm().add_handler(handler)
                logger.info("LangChain auto-instrumentation enabled (legacy callback manager)")
            except (ImportError, AttributeError):
                logger.info(
                    "LangChain detected but global callback manager unavailable. "
                    "Attach CMACallbackHandler manually: "
                    "llm = ChatOpenAI(callbacks=[CMACallbackHandler(tracker)])"
                )
    except ImportError:
        pass  # langchain not installed

    if not _atexit_registered:
        atexit.register(stop)
        _atexit_registered = True

    return _tracker


def stop() -> None:
    """Flush and close the active tracker."""
    global _tracker
    if _tracker is not None:
        _tracker.close()
        _tracker = None


def get_tracker() -> CostTracker | None:
    return _tracker
