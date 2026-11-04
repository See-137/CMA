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
    default_workflow: str | None = None,
    default_swarm: str | None = None,
    flush_interval: float = 5.0,
    inject_stream_usage: bool = True,
    enforce_budgets: bool = False,
    budget_fail_closed: bool = False,
    budget_check_ttl: float = 10.0,
    budget_check_timeout: float = 2.0,
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

    enforce_budgets: when True, every patched OpenAI/Anthropic call first asks
    the backend (GET /api/v1/budgets/check, cached for budget_check_ttl
    seconds) whether a blocking budget matching ``default_agent`` is
    exhausted, and raises :class:`cost_monitor.BudgetExceeded` before the
    provider is contacted. Spend is always recorded either way. If CMA is
    unreachable the call proceeds (fail-open) unless budget_fail_closed=True.
    Calls made between checks can overshoot a limit by up to one TTL's worth
    of spend. An uncached check blocks the calling thread for at most
    budget_check_timeout seconds (async callers wait in a worker thread).

    Attribution: patched calls are recorded under ``default_agent`` unless made
    inside ``with attribution(agent=..., workflow=...)``.

    LangChain: models built on the OpenAI or Anthropic clients are recorded by
    these patches. For any other model, attach the handler yourself (it records
    each call once either way; langchain_core has no global callback manager)::

        from cost_monitor._langchain import CMACallbackHandler
        llm = SomeChatModel(callbacks=[CMACallbackHandler(tracker)])
    """
    global _tracker, _atexit_registered

    if _tracker is not None:
        _tracker.close()

    _tracker = CostTracker(
        endpoint=endpoint,
        api_key=api_key,
        default_agent=default_agent,
        default_workflow=default_workflow,
        default_swarm=default_swarm,
        flush_interval=flush_interval,
        budget_check_ttl=budget_check_ttl,
        budget_check_timeout=budget_check_timeout,
        budget_fail_closed=budget_fail_closed,
    )
    _patching.set_active_tracker(_tracker)
    _patching.config["inject_stream_usage"] = inject_stream_usage
    _patching.config["enforce_budgets"] = enforce_budgets

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
