"""Zero-config auto-instrumentation for LLM libraries."""

import atexit
import logging

from cost_monitor import _patching
from cost_monitor.tracker import CostTracker

logger = logging.getLogger("cost_monitor.auto")

_tracker: CostTracker | None = None
_atexit_registered = False  # guard against stacking atexit callbacks on repeated calls


def auto_instrument(
    endpoint: str,
    api_key: str,
    default_agent: str = "auto",
    flush_interval: float = 5.0,
    inject_stream_usage: bool = True,
) -> CostTracker:
    """Instrument all installed LLM libraries (OpenAI, Anthropic, LangChain).

    Safe to call multiple times: patches are installed once (idempotent —
    wrappers resolve the active tracker at call time), and the previous
    tracker is closed and replaced.

    inject_stream_usage: when True (default), OpenAI streaming requests get
    stream_options={"include_usage": True} injected so the final usage chunk
    is emitted. Set False if your endpoint rejects stream_options (some
    Azure OpenAI api-versions) — streaming usage then depends on the caller
    passing the option themselves.

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
    _patching.set_active_tracker(_tracker)
    _patching.config["inject_stream_usage"] = inject_stream_usage

    # Patch OpenAI if installed
    try:
        from cost_monitor._openai import patch_openai

        patch_openai()
        logger.info("OpenAI auto-instrumentation enabled")
    except ImportError:
        pass

    # Patch Anthropic if installed
    try:
        from cost_monitor._anthropic import patch_anthropic

        patch_anthropic()
        logger.info("Anthropic auto-instrumentation enabled")
    except ImportError:
        pass

    # Register LangChain global callback if langchain_core or langchain is
    # installed. On modern langchain_core the global manager is unavailable
    # and this falls through to the manual-attach advisory.
    try:
        from cost_monitor._langchain import CMACallbackHandler

        handler = CMACallbackHandler(_tracker)

        try:
            from langchain_core.callbacks import get_callback_manager  # type: ignore[attr-defined]

            get_callback_manager().add_handler(handler, inherit=True)
            logger.info(
                "LangChain auto-instrumentation enabled (global callback manager)"
            )
        except (ImportError, AttributeError):
            try:
                from langchain.callbacks import get_callback_manager as _gcm  # type: ignore[attr-defined]

                _gcm().add_handler(handler)
                logger.info(
                    "LangChain auto-instrumentation enabled (legacy callback manager)"
                )
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


def uninstrument() -> None:
    """Restore every patched SDK method to its pristine original."""
    _patching.uninstall_all()


def stop() -> None:
    """Unpatch, then flush and close the active tracker.

    Unpatching first means no new events can arrive through wrappers while
    the tracker drains, and no patch is ever left pointing at a closed
    tracker.
    """
    global _tracker
    uninstrument()
    _patching.set_active_tracker(None)
    if _tracker is not None:
        _tracker.close()
        _tracker = None


def get_tracker() -> CostTracker | None:
    return _tracker
