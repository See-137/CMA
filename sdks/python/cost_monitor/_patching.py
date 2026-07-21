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
from typing import TYPE_CHECKING, Any, AsyncIterator, Iterator

if TYPE_CHECKING:
    from cost_monitor.tracker import CostTracker

logger = logging.getLogger("cost_monitor.patching")

_active_tracker: CostTracker | None = None

# Behavior toggles set by auto_instrument().
config: dict[str, Any] = {"inject_stream_usage": True}

# key -> (owner, attr_name, pristine_original, our_wrapper)
_originals: dict[str, tuple[Any, str, Any, Any]] = {}


def set_active_tracker(tracker: CostTracker | None) -> None:
    global _active_tracker
    _active_tracker = tracker


def get_active_tracker() -> CostTracker | None:
    return _active_tracker


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


class InstrumentedStream:
    """Delegating proxy over an SDK Stream object.

    Iteration goes through the instrumented generator (which records usage
    in its finally block); everything else — close(), .response, context
    manager protocol, isinstance-adjacent attribute access — forwards to
    the underlying SDK stream, preserving its public contract.
    """

    def __init__(self, stream: Any, instrumented_iter: Iterator[Any]) -> None:
        self._cma_stream = stream
        self._cma_iter = instrumented_iter

    def __iter__(self) -> Iterator[Any]:
        return self._cma_iter

    def __next__(self) -> Any:
        return next(self._cma_iter)

    def __enter__(self) -> InstrumentedStream:
        enter = getattr(self._cma_stream, "__enter__", None)
        if enter is not None:
            enter()
        return self

    def __exit__(self, *exc: Any) -> Any:
        exit_ = getattr(self._cma_stream, "__exit__", None)
        if exit_ is not None:
            return exit_(*exc)
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cma_stream, name)


class InstrumentedAsyncStream:
    """Async counterpart of InstrumentedStream."""

    def __init__(self, stream: Any, instrumented_iter: AsyncIterator[Any]) -> None:
        self._cma_stream = stream
        self._cma_iter = instrumented_iter

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._cma_iter

    async def __anext__(self) -> Any:
        return await self._cma_iter.__anext__()

    async def __aenter__(self) -> InstrumentedAsyncStream:
        enter = getattr(self._cma_stream, "__aenter__", None)
        if enter is not None:
            await enter()
        return self

    async def __aexit__(self, *exc: Any) -> Any:
        exit_ = getattr(self._cma_stream, "__aexit__", None)
        if exit_ is not None:
            return await exit_(*exc)
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cma_stream, name)
