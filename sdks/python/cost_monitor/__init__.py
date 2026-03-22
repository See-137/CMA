"""CMA Cost Monitor — Python SDK for tracking LLM costs in multi-agent systems."""

from cost_monitor.tracker import CostTracker, RequestContext
from cost_monitor.auto import auto_instrument, stop as stop_instrumentation

__version__ = "0.1.0"
__all__ = [
    "CostTracker",
    "RequestContext",
    "auto_instrument",
    "stop_instrumentation",
    "__version__",
]
