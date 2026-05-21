"""LangChain callback handler for CMA cost tracking."""

import time
import logging
import uuid
from typing import Any

from cost_monitor.tracker import CostTracker

logger = logging.getLogger("cost_monitor.langchain")

# Defensive import: try langchain_core first (preferred), fall back to legacy langchain.
try:
    from langchain_core.callbacks import BaseCallbackHandler as _BaseCallbackHandler
except ImportError:
    try:
        from langchain.callbacks.base import BaseCallbackHandler as _BaseCallbackHandler  # type: ignore[no-redef]
    except ImportError:
        # Neither package installed; define a no-op base so the class can be defined
        # without raising at import time. Instantiation without langchain will still fail
        # when LangChain itself tries to use the handler, which is the correct behavior.
        class _BaseCallbackHandler:  # type: ignore[no-redef]
            raise_error = False


def _extract_model(serialized: dict, invocation_params: dict, metadata: dict) -> str:
    """Extract model name from LangChain callback arguments.

    LangChain passes model info in several places depending on the LLM class;
    we check the most common locations in priority order.
    """
    # 1. invocation_params is the most reliable for most integrations
    for key in ("model_name", "model", "model_id"):
        val = invocation_params.get(key)
        if val:
            return str(val)
    # 2. serialized["kwargs"] is used by some LLM classes
    serialized_kwargs = serialized.get("kwargs", {}) if serialized else {}
    for key in ("model_name", "model", "model_id"):
        val = serialized_kwargs.get(key)
        if val:
            return str(val)
    # 3. metadata dict (set by some newer integrations)
    if metadata:
        for key in ("model_name", "model"):
            val = metadata.get(key)
            if val:
                return str(val)
    return "unknown"


class CMACallbackHandler(_BaseCallbackHandler):
    """LangChain callback handler that sends cost events to CMA.

    Usage::

        from cost_monitor._langchain import CMACallbackHandler
        handler = CMACallbackHandler(tracker)
        llm = ChatOpenAI(callbacks=[handler])
    """

    def __init__(
        self,
        tracker: CostTracker,
        agent_name: str = "langchain",
        workflow: str | None = None,
    ):
        super().__init__()
        self.tracker = tracker
        self.agent_name = agent_name
        self.workflow = workflow
        self._runs: dict[str, dict[str, Any]] = {}

    def on_llm_start(
        self,
        serialized: dict,
        prompts: list[str],
        *,
        run_id: Any = None,
        invocation_params: dict | None = None,
        metadata: dict | None = None,
        **kwargs: Any,
    ) -> None:
        rid = str(run_id) if run_id is not None else str(uuid.uuid4())
        model = _extract_model(
            serialized or {},
            invocation_params or kwargs.get("invocation_params", {}),
            metadata or {},
        )
        self._runs[rid] = {"start": time.time(), "model": model}

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        rid = str(run_id) if run_id is not None else ""
        run_data = self._runs.pop(rid, {})
        start = run_data.get("start", time.time())
        model = run_data.get("model", "unknown")
        duration_ms = int((time.time() - start) * 1000)

        token_usage: dict = {}
        if hasattr(response, "llm_output") and response.llm_output:
            token_usage = response.llm_output.get("token_usage", {})

        # Infer provider from model name
        model_lower = model.lower()
        if "claude" in model_lower or "anthropic" in model_lower:
            provider = "anthropic"
        elif "gemini" in model_lower or "google" in model_lower:
            provider = "google"
        else:
            provider = "openai"

        # Accept both OpenAI-style and Anthropic-style token field names
        tokens_in = (
            token_usage.get("prompt_tokens")
            or token_usage.get("input_tokens")
            or 0
        )
        tokens_out = (
            token_usage.get("completion_tokens")
            or token_usage.get("output_tokens")
            or 0
        )

        self.tracker.log_event(
            agent_name=self.agent_name,
            model=model,
            provider=provider,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            duration_ms=duration_ms,
            status="success",
            workflow=self.workflow,
            trace_id=rid,
        )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        rid = str(run_id) if run_id is not None else ""
        run_data = self._runs.pop(rid, {})
        start = run_data.get("start", time.time())
        model = run_data.get("model", "unknown")
        duration_ms = int((time.time() - start) * 1000)

        self.tracker.log_event(
            agent_name=self.agent_name,
            model=model,
            provider="unknown",
            tokens_input=0,
            tokens_output=0,
            duration_ms=duration_ms,
            status="failure",
            metadata={"error": str(error)},
            trace_id=rid,
        )
