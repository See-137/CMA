"""Shared patching machinery: active-tracker registry, idempotent installs,
and SDK stream proxies.

Design notes:

- Wrappers resolve the tracker via get_active_tracker() at CALL time rather
  than closing over it at patch time. Repeated auto_instrument() calls swap
  the active tracker without re-patching, so wrappers can never stack and
  never point at a closed tracker.
- install() refuses to wrap anything already carrying the _cma_patched
  marker and records the pristine original; uninstall_all() restores every
  patched attribute exactly as found.
- Patching is process-global by nature. Other instrumentation libraries
  (OpenTelemetry, LangSmith) patch the same methods — last patcher wins;
  that is a documented limitation of the approach, not something this
  module can fix.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Iterator

if TYPE_CHECKING:
    from cost_monitor.tracker import CostTracker

logger = logging.getLogger("cost_monitor.patching")

_active_tracker: CostTracker | None = None

# Behavior toggles set by auto_instrument().
config: dict[str, Any] = {"inject_stream_usage": True, "enforce_budgets": False}

# key -> (owner, attr_name, pristine_original, our_wrapper)
_originals: dict[str, tuple[Any, str, Any, Any]] = {}


def set_active_tracker(tracker: CostTracker | None) -> None:
    global _active_tracker
    _active_tracker = tracker


def get_active_tracker() -> CostTracker | None:
    return _active_tracker


def precall_enforce(
    tracker: CostTracker, provider: str | None = None, model: str | None = None
) -> None:
    """Pre-call budget enforcement for patched calls (opt-in). Raises
    BudgetExceeded before the provider is contacted; nothing is logged as a
    failed call because no call was made."""
    if config.get("enforce_budgets"):
        tracker.enforce_budget(provider=provider, model=model)


async def aprecall_enforce(
    tracker: CostTracker, provider: str | None = None, model: str | None = None
) -> None:
    """Async variant: the (cached) HTTP check runs in a worker thread so an
    uncached check never blocks the event loop."""
    if config.get("enforce_budgets"):
        if tracker.enforce_budget_if_cached(provider, model):
            return  # fresh cached answer: no thread hop
        import asyncio

        await asyncio.to_thread(tracker.enforce_budget, provider=provider, model=model)


def install(owner: Any, attr: str, wrapper: Any, key: str) -> bool:
    """Install wrapper over owner.attr unless our wrapper is already there.

    Returns True if installed, False if skipped (already patched by us).
    """
    current = getattr(owner, attr)
    if getattr(current, "_cma_patched", False):
        return False
    wrapper._cma_patched = True
    _originals[key] = (owner, attr, current, wrapper)
    setattr(owner, attr, wrapper)
    return True


def uninstall_all() -> None:
    """Restore every patched attribute to its pristine original.

    Symmetric with install()'s defensiveness: if another library patched on
    top of our wrapper after us, restoring "our" original would silently
    destroy THEIR instrumentation — so we leave the attribute alone (our
    buried wrapper degrades to a passthrough once the active tracker is
    cleared) and warn instead of clobbering.
    """
    for key, (owner, attr, original, wrapper) in _originals.items():
        current = getattr(owner, attr, None)
        if current is not wrapper:
            logger.warning(
                "cost_monitor: %s was re-patched by another library after us "
                "— leaving it installed instead of clobbering it. Our buried "
                "wrapper becomes a passthrough.",
                key,
            )
            continue
        setattr(owner, attr, original)
    _originals.clear()


def is_cancellation(exc: BaseException) -> bool:
    """Task cancellation or Ctrl-C: the caller stopped the call, which is an
    incomplete call (tokens already reported stay billed), not a failure."""
    import asyncio

    return isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt))


class StreamState:
    """Shared between an instrumented stream generator and its proxy.

    ``started``: the generator body has begun (set on the first item).
    ``closed_by_caller``: the caller closed the stream; an error the reading
    side then sees is the close, so it is recorded as incomplete, not failure.
    ``on_unread``: records a stream that was closed without ever being read
    (closing a never-started generator does not run its ``finally``).
    """

    __slots__ = ("closed_by_caller", "on_unread", "started")

    def __init__(self, on_unread: Callable[[], None] | None = None) -> None:
        self.started = False
        self.closed_by_caller = False
        self.on_unread = on_unread

    def finish_unread(self) -> bool:
        """Record a never-read stream once; True if this call did."""
        if self.started or self.on_unread is None:
            return False
        callback, self.on_unread = self.on_unread, None
        callback()
        return True


class InstrumentedStream:
    """Delegating proxy over an SDK Stream object.

    Iteration goes through the instrumented generator (which records usage
    in its finally block); everything else — .response, attribute access —
    forwards to the underlying SDK stream, preserving its public contract.
    close()/__exit__ record the call before closing the SDK stream.
    """

    def __init__(
        self,
        stream: Any,
        instrumented_iter: Iterator[Any],
        state: StreamState | None = None,
    ) -> None:
        self._cma_stream = stream
        self._cma_iter = instrumented_iter
        self._cma_state = state or StreamState()

    def __iter__(self) -> Iterator[Any]:
        return self._cma_iter

    def __next__(self) -> Any:
        return next(self._cma_iter)

    def __enter__(self) -> InstrumentedStream:
        enter = getattr(self._cma_stream, "__enter__", None)
        if enter is not None:
            enter()
        return self

    def _cma_finish(self) -> None:
        """Record the call now, not whenever the proxy is garbage-collected.

        Never raises, so the SDK close that follows always runs. If another
        thread is inside next(), the generator cannot be closed from here;
        the SDK close below ends that read and the reading thread records it.
        """
        state = self._cma_state
        state.closed_by_caller = True
        try:
            if state.finish_unread():
                pass
            elif getattr(self._cma_iter, "gi_running", False):
                return
            close = getattr(self._cma_iter, "close", None)
            if close is not None:
                close()
        except Exception:
            logger.exception("cost_monitor: failed to record a closed stream")

    def close(self) -> Any:
        self._cma_finish()
        close = getattr(self._cma_stream, "close", None)
        return close() if close is not None else None

    def __exit__(self, *exc: Any) -> Any:
        self._cma_finish()
        exit_ = getattr(self._cma_stream, "__exit__", None)
        if exit_ is not None:
            return exit_(*exc)
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cma_stream, name)


class InstrumentedAsyncStream:
    """Async counterpart of InstrumentedStream."""

    def __init__(
        self,
        stream: Any,
        instrumented_iter: AsyncIterator[Any],
        state: StreamState | None = None,
    ) -> None:
        self._cma_stream = stream
        self._cma_iter = instrumented_iter
        self._cma_state = state or StreamState()

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._cma_iter

    async def __anext__(self) -> Any:
        return await self._cma_iter.__anext__()

    async def __aenter__(self) -> InstrumentedAsyncStream:
        enter = getattr(self._cma_stream, "__aenter__", None)
        if enter is not None:
            await enter()
        return self

    async def _cma_finish(self) -> None:
        """See InstrumentedStream._cma_finish. ``ag_running`` is true while
        another task is suspended inside ``__anext__``."""
        state = self._cma_state
        state.closed_by_caller = True
        try:
            if state.finish_unread():
                pass
            elif getattr(self._cma_iter, "ag_running", False):
                return
            aclose = getattr(self._cma_iter, "aclose", None)
            if aclose is not None:
                await aclose()
        except Exception:
            logger.exception("cost_monitor: failed to record a closed stream")

    async def close(self) -> Any:
        await self._cma_finish()
        close = getattr(self._cma_stream, "close", None)
        return await close() if close is not None else None

    async def __aexit__(self, *exc: Any) -> Any:
        await self._cma_finish()
        exit_ = getattr(self._cma_stream, "__aexit__", None)
        if exit_ is not None:
            return await exit_(*exc)
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cma_stream, name)
