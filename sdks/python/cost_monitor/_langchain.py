"""LangChain callback handler for CMA cost tracking."""

import time
import logging
import uuid
from typing import Any

from cost_monitor.tracker import CostTracker

logger = logging.getLogger("cost_monitor.langchain")


class CMACallbackHandler:
    """LangChain callback handler that sends cost events to CMA.

    Usage:
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
        self.tracker = tracker
        self.agent_name = agent_name
        self.workflow = workflow
        self._runs: dict[str, dict[str, Any]] = {}

    def on_llm_start(
        self,
        serialized: dict,
        prompts: list[str],
        *,
        run_id: str | None = None,
        **kwargs,
    ) -> None:
        rid = run_id or str(uuid.uuid4())
        self._runs[rid] = {
            "start": time.time(),
            "model": kwargs.get("invocation_params", {}).get("model_name", "unknown"),
        }

    def on_llm_end(self, response: Any, *, run_id: str | None = None, **kwargs) -> None:
        rid = run_id or ""
        run_data = self._runs.pop(rid, {})
        start = run_data.get("start", time.time())
        model = run_data.get("model", "unknown")
        duration_ms = int((time.time() - start) * 1000)

        token_usage = {}
        if hasattr(response, "llm_output") and response.llm_output:
            token_usage = response.llm_output.get("token_usage", {})

        provider = "openai"  # default, LangChain mostly wraps OpenAI
        if "claude" in model.lower() or "anthropic" in model.lower():
            provider = "anthropic"
        elif "gemini" in model.lower():
            provider = "google"

        self.tracker.log_event(
            agent_name=self.agent_name,
            model=model,
            provider=provider,
            tokens_input=token_usage.get("prompt_tokens", 0),
            tokens_output=token_usage.get("completion_tokens", 0),
            duration_ms=duration_ms,
            status="success",
            workflow=self.workflow,
            trace_id=rid,
        )

    def on_llm_error(
        self, error: BaseException, *, run_id: str | None = None, **kwargs
    ) -> None:
        rid = run_id or ""
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
