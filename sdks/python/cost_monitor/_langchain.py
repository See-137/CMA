"""LangChain callback handler for CMA cost tracking."""

import time
import logging
import uuid
from typing import Any

from cost_monitor._usage import _int, usage_fields
from cost_monitor.tracker import CostTracker, _scope

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


def _provider_of(metadata: dict, model: str) -> str:
    """LangSmith metadata names the vendor; otherwise infer it from the model."""
    named = str((metadata or {}).get("ls_provider") or "").lower()
    if named:
        return "openai" if named in ("azure", "azure_openai") else named
    model_lower = model.lower()
    if "claude" in model_lower or "anthropic" in model_lower:
        return "anthropic"
    if "gemini" in model_lower or "google" in model_lower:
        return "google"
    return "openai"


def _message_usage(response: Any) -> dict | None:
    """Usage from the modern ``usage_metadata`` on each generated message.

    LangChain normalises it for every vendor: ``input_tokens`` already counts
    cached input, with the parts under ``input_token_details``. Returns CMA
    field names, or None when no message carries it.
    """
    totals = {
        "tokens_input": 0,
        "tokens_output": 0,
        "tokens_cache_read": 0,
        "tokens_cache_write": 0,
        "tokens_cache_write_1h": 0,
    }
    found = False
    for batch in getattr(response, "generations", None) or []:
        for generation in batch:
            usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if not usage:
                continue
            found = True
            details = usage.get("input_token_details") or {}
            totals["tokens_input"] += _int(usage.get("input_tokens"))
            totals["tokens_output"] += _int(usage.get("output_tokens"))
            totals["tokens_cache_read"] += _int(details.get("cache_read"))
            ephemeral_1h = _int(details.get("ephemeral_1h_input_tokens"))
            ephemeral_5m = _int(details.get("ephemeral_5m_input_tokens"))
            totals["tokens_cache_write"] += (
                _int(details.get("cache_creation")) or ephemeral_1h + ephemeral_5m
            )
            totals["tokens_cache_write_1h"] += ephemeral_1h
    return totals if found else None


class CMACallbackHandler(_BaseCallbackHandler):
    """LangChain callback handler that sends cost events to CMA.

    Usage::

        from cost_monitor._langchain import CMACallbackHandler
        handler = CMACallbackHandler(tracker)
        llm = ChatOpenAI(callbacks=[handler])

    Safe next to ``auto_instrument``: when a patched client records the call,
    the handler adds nothing, and the patched event takes the handler's
    ``agent_name`` and ``workflow``. Each call yields one event.
    """

    # Run in the model's own context, not an executor's copy: the patched
    # client must see the marker set in on_*_start, on async paths too.
    run_inline = True

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

    def _start(self, serialized, run_id, invocation_params, metadata, kwargs) -> None:
        rid = str(run_id) if run_id is not None else str(uuid.uuid4())
        metadata = metadata or {}
        model = str(metadata.get("ls_model_name") or "") or _extract_model(
            serialized or {},
            invocation_params or kwargs.get("invocation_params") or {},
            metadata,
        )
        previous = _scope.get()
        marker = {"recorded": False}
        scope = {
            "agent": self.agent_name,
            "trace_id": rid,
            "_llm_run": marker,
            **(previous or {}),
        }
        # This run's marker, not an enclosing run's. The scope's own agent wins
        # over the handler's default, as an explicit attribution() block should.
        scope["_llm_run"] = marker
        scope["trace_id"] = rid
        if self.workflow and not scope.get("workflow"):
            scope["workflow"] = self.workflow
        _scope.set(scope)
        self._runs[rid] = {
            "start": time.time(),
            "model": model,
            "provider": _provider_of(metadata, model),
            "marker": marker,
            "previous": previous,
            "scope": scope,
        }

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
        self._start(serialized, run_id, invocation_params, metadata, kwargs)

    def on_chat_model_start(
        self,
        serialized: dict,
        messages: list,
        *,
        run_id: Any = None,
        invocation_params: dict | None = None,
        metadata: dict | None = None,
        **kwargs: Any,
    ) -> None:
        self._start(serialized, run_id, invocation_params, metadata, kwargs)

    def _finish(self, run_id: Any) -> tuple[str, dict[str, Any]]:
        rid = str(run_id) if run_id is not None else ""
        run = self._runs.pop(rid, {})
        # Only undo our own scope: with a callback running in a copied context
        # this may be another run's, which is not ours to replace.
        if run and _scope.get() is run["scope"]:
            _scope.set(run["previous"])
        return rid, run

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        rid, run_data = self._finish(run_id)
        if run_data.get("marker", {}).get("recorded"):
            return  # the patched client already recorded this call (SDK-04)
        start = run_data.get("start", time.time())
        model = run_data.get("model", "unknown")
        duration_ms = int((time.time() - start) * 1000)

        fields = _message_usage(response)
        token_usage: Any = {}
        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, dict):
            if model == "unknown":
                model = str(llm_output.get("model_name") or llm_output.get("model") or model)
            token_usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        if fields is None:
            # Older integrations: vendor-shaped totals on llm_output, including
            # prompt-cache counts (Anthropic's are added to the input total).
            fields = usage_fields(token_usage if isinstance(token_usage, dict) else None)
        if model == "unknown":
            model = _generation_model(response)

        self.tracker.log_event(
            agent_name=run_data.get("scope", {}).get("agent", self.agent_name),
            model=model,
            provider=run_data.get("provider") or _provider_of({}, model),
            tokens_input=fields["tokens_input"],
            tokens_output=fields["tokens_output"],
            tokens_cache_read=fields["tokens_cache_read"],
            tokens_cache_write=fields["tokens_cache_write"],
            tokens_cache_write_1h=fields["tokens_cache_write_1h"],
            duration_ms=duration_ms,
            status="success",
            workflow=run_data.get("scope", {}).get("workflow", self.workflow),
            trace_id=rid,
        )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        rid, run_data = self._finish(run_id)
        if run_data.get("marker", {}).get("recorded"):
            return
        start = run_data.get("start", time.time())
        model = run_data.get("model", "unknown")
        duration_ms = int((time.time() - start) * 1000)

        self.tracker.log_event(
            agent_name=run_data.get("scope", {}).get("agent", self.agent_name),
            model=model,
            provider=run_data.get("provider") or "unknown",
            tokens_input=0,
            tokens_output=0,
            duration_ms=duration_ms,
            status="failure",
            workflow=run_data.get("scope", {}).get("workflow", self.workflow),
            metadata={"error": str(error)[:500]},
            trace_id=rid,
        )


def _generation_model(response: Any) -> str:
    for batch in getattr(response, "generations", None) or []:
        for generation in batch:
            meta = getattr(getattr(generation, "message", None), "response_metadata", None)
            if isinstance(meta, dict):
                name = meta.get("model_name") or meta.get("model")
                if name:
                    return str(name)
    return "unknown"
