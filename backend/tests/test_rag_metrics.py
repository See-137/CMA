"""Spec for RAG pipeline metrics (PR B).

Contract under test:
- cma_rag_stage_seconds{stage=intent|embed_query|vector_search|sql_queries|
  llm_first_token|llm_total} — stage names are 1:1 with future OTel span
  names (the documented migration path), so they are a published contract.
- cma_chat_requests_total{mode=sync|stream, outcome=ok|llm_error|cancelled}
  — counted at the SSE serialization point. THE metric that matters:
  streaming LLM failures ship as {"type": "error"} data events inside an
  HTTP 200, invisible to every status-code-based monitor.

LLM provider and retrieval are stubbed (their logic is owned elsewhere);
counters are asserted as deltas against the process-global registry.
"""

from __future__ import annotations

from types import SimpleNamespace

from prometheus_client import REGISTRY


def _val(name: str, labels: dict | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0


def _stub_retrieval(monkeypatch) -> None:
    from app.services.rag_engine import RetrievalContext

    import app.api.chat as chat_api

    monkeypatch.setattr(chat_api, "retrieve", lambda msg, db: RetrievalContext())


class _OkProvider:
    async def generate(self, messages):
        return SimpleNamespace(content="42 dollars", model="fake-model", usage={})

    async def stream(self, messages):
        yield "42 "
        yield "dollars"


class _BoomProvider:
    async def generate(self, messages):
        raise RuntimeError("LLM exploded")

    async def stream(self, messages):
        yield "42 "
        raise RuntimeError("LLM exploded mid-stream")


def _stub_provider(monkeypatch, provider) -> None:
    import app.api.chat as chat_api

    monkeypatch.setattr(chat_api, "get_llm_provider", lambda: provider)


def test_sync_chat_counts_ok_and_llm_total(authed_client, monkeypatch):
    _stub_retrieval(monkeypatch)
    _stub_provider(monkeypatch, _OkProvider())

    ok_before = _val("cma_chat_requests_total", {"mode": "sync", "outcome": "ok"})
    llm_total_before = _val("cma_rag_stage_seconds_count", {"stage": "llm_total"})

    resp = authed_client.post("/api/v1/chat", json={"message": "how much?"})
    assert resp.status_code == 200
    assert resp.json()["answer"] == "42 dollars"

    assert (
        _val("cma_chat_requests_total", {"mode": "sync", "outcome": "ok"})
        == ok_before + 1
    )
    assert (
        _val("cma_rag_stage_seconds_count", {"stage": "llm_total"})
        == llm_total_before + 1
    )


def test_sync_chat_llm_failure_counts_llm_error(authed_client, monkeypatch):
    """Failure counts the outcome — but must NOT observe llm_total:
    time-to-failure samples (instant 401s during an outage) would collapse
    the latency percentiles into 'faster than ever' mid-incident."""
    _stub_retrieval(monkeypatch)
    _stub_provider(monkeypatch, _BoomProvider())

    err_before = _val(
        "cma_chat_requests_total", {"mode": "sync", "outcome": "llm_error"}
    )
    llm_total_before = _val("cma_rag_stage_seconds_count", {"stage": "llm_total"})

    resp = authed_client.post("/api/v1/chat", json={"message": "how much?"})
    assert resp.status_code == 502

    assert (
        _val("cma_chat_requests_total", {"mode": "sync", "outcome": "llm_error"})
        == err_before + 1
    )
    assert (
        _val("cma_rag_stage_seconds_count", {"stage": "llm_total"}) == llm_total_before
    ), "llm_total must not record time-to-failure samples"


def test_stream_chat_counts_ok_first_token_and_total(authed_client, monkeypatch):
    _stub_retrieval(monkeypatch)
    _stub_provider(monkeypatch, _OkProvider())

    ok_before = _val("cma_chat_requests_total", {"mode": "stream", "outcome": "ok"})
    first_before = _val("cma_rag_stage_seconds_count", {"stage": "llm_first_token"})
    total_before = _val("cma_rag_stage_seconds_count", {"stage": "llm_total"})

    resp = authed_client.post("/api/v1/chat/stream", json={"message": "how much?"})
    assert resp.status_code == 200
    assert '"type": "done"' in resp.text

    assert (
        _val("cma_chat_requests_total", {"mode": "stream", "outcome": "ok"})
        == ok_before + 1
    )
    assert (
        _val("cma_rag_stage_seconds_count", {"stage": "llm_first_token"})
        == first_before + 1
    )
    assert (
        _val("cma_rag_stage_seconds_count", {"stage": "llm_total"}) == total_before + 1
    )


def test_stream_llm_failure_is_counted_despite_http_200(authed_client, monkeypatch):
    """THE blind-spot test: the LLM dies mid-stream, the client still gets
    an HTTP 200 with an {"type": "error"} data event — status-code monitors
    see nothing. The outcome counter at the serialization point must."""
    _stub_retrieval(monkeypatch)
    _stub_provider(monkeypatch, _BoomProvider())

    err_before = _val(
        "cma_chat_requests_total", {"mode": "stream", "outcome": "llm_error"}
    )
    ok_before = _val("cma_chat_requests_total", {"mode": "stream", "outcome": "ok"})

    llm_total_before = _val("cma_rag_stage_seconds_count", {"stage": "llm_total"})

    resp = authed_client.post("/api/v1/chat/stream", json={"message": "how much?"})
    assert resp.status_code == 200, "SSE errors ship inside a 200 — that's the point"
    assert '"type": "error"' in resp.text
    # The frontend keys on the done event to terminate the stream — it must
    # arrive on the error path too (yield-in-except -> finally -> done).
    assert '"type": "done"' in resp.text

    assert (
        _val("cma_chat_requests_total", {"mode": "stream", "outcome": "llm_error"})
        == err_before + 1
    )
    assert (
        _val("cma_chat_requests_total", {"mode": "stream", "outcome": "ok"})
        == ok_before
    )
    assert (
        _val("cma_rag_stage_seconds_count", {"stage": "llm_total"}) == llm_total_before
    ), "llm_total must not record time-to-failure samples"


def test_retrieve_observes_all_pipeline_stages(monkeypatch):
    """retrieve() times its four stages regardless of what the query needs —
    an empty intent still passes through every stage block."""
    import app.services.rag_engine as rag

    from tests.conftest import TestSession

    monkeypatch.setattr(rag, "detect_query_intent", lambda q, db: rag.QueryIntent())
    monkeypatch.setattr(rag, "embed_text", lambda q: [0.0] * 384)
    monkeypatch.setattr(rag, "search_events", lambda *a, **k: [])
    monkeypatch.setattr(rag, "search_rollups", lambda *a, **k: [])

    stages = ("intent", "embed_query", "vector_search", "sql_queries")
    before = {s: _val("cma_rag_stage_seconds_count", {"stage": s}) for s in stages}

    db = TestSession()
    try:
        rag.retrieve("how much did we spend", db)
    finally:
        db.close()

    for s in stages:
        assert _val("cma_rag_stage_seconds_count", {"stage": s}) == before[s] + 1, (
            f"stage {s} was not observed"
        )


def test_stage_and_outcome_children_are_zero_initialized(authed_client):
    """Bounded label sets are pre-created so the first error after deploy is
    visible to rate()/increase() — same convention as PR A counters.

    Checks are FAMILY-SCOPED (get_sample_value on the exact metric name) —
    a raw substring match on stage="..." could be satisfied by any other
    stage-labeled family and mask a missing child.
    """
    resp = authed_client.get("/metrics")
    assert resp.status_code == 200
    for stage in (
        "intent",
        "embed_query",
        "vector_search",
        "sql_queries",
        "llm_first_token",
        "llm_total",
    ):
        assert (
            REGISTRY.get_sample_value("cma_rag_stage_seconds_count", {"stage": stage})
            is not None
        ), f"stage child {stage} not initialized"
    for mode in ("sync", "stream"):
        for outcome in ("ok", "llm_error", "cancelled"):
            assert (
                REGISTRY.get_sample_value(
                    "cma_chat_requests_total", {"mode": mode, "outcome": outcome}
                )
                is not None
            ), f"chat counter child {mode}/{outcome} not initialized"


def test_stream_client_disconnect_counts_cancelled(monkeypatch):
    """Client disconnects mid-stream: the generator gets closed at a yield
    point, the (GeneratorExit, CancelledError) branch fires, and the
    request lands in the cancelled bucket — with NO llm_total observation
    (truncated durations would poison the latency percentiles) and no
    done event. Driven directly via the ASGI body_iterator: anext twice
    (citations + first content), then aclose."""
    import asyncio

    import app.api.chat as chat_api
    from app.schemas.schemas import ChatRequest
    from app.services.rag_engine import RetrievalContext

    monkeypatch.setattr(chat_api, "retrieve", lambda msg, db: RetrievalContext())
    monkeypatch.setattr(chat_api, "get_llm_provider", lambda: _OkProvider())

    cancelled_before = _val(
        "cma_chat_requests_total", {"mode": "stream", "outcome": "cancelled"}
    )
    ok_before = _val("cma_chat_requests_total", {"mode": "stream", "outcome": "ok"})
    llm_total_before = _val("cma_rag_stage_seconds_count", {"stage": "llm_total"})

    async def scenario() -> list[str]:
        response = await chat_api.chat_stream(ChatRequest(message="how much?"), db=None)
        agen = response.body_iterator
        frames = [await agen.__anext__(), await agen.__anext__()]
        await agen.aclose()
        return frames

    frames = asyncio.run(scenario())
    assert '"type": "citations"' in frames[0]
    assert '"type": "content"' in frames[1]

    assert (
        _val("cma_chat_requests_total", {"mode": "stream", "outcome": "cancelled"})
        == cancelled_before + 1
    )
    assert (
        _val("cma_chat_requests_total", {"mode": "stream", "outcome": "ok"})
        == ok_before
    )
    assert (
        _val("cma_rag_stage_seconds_count", {"stage": "llm_total"}) == llm_total_before
    ), "cancelled streams must not observe llm_total"
