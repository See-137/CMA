"""RAG chat endpoints — non-streaming and SSE streaming."""

import asyncio
import json
import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from httpx import HTTPStatusError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings
from app.metrics import CHAT_REQUESTS, RAG_STAGE_SECONDS
from app.schemas.schemas import ChatRequest, ChatResponse, CitationOut
from app.services.llm_provider import get_llm_provider
from app.services.rag_engine import build_messages, retrieve, verified_citations
from app.services.rate_limit import rate_limit
from app.services.vector_store import get_events_collection, get_rollups_collection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

_chat_limit = rate_limit("chat", lambda: settings.RATE_LIMIT_CHAT_PER_MINUTE)


async def _retrieve_async(message: str, db: Session):
    """Run the blocking retrieval (SQL + CPU-bound embedding) off the event loop."""
    return await asyncio.to_thread(retrieve, message, db)


_LLM_ERROR_MAP = {
    401: "LLM API key is invalid or expired. Check CMA_OPENAI_API_KEY.",
    403: "LLM API key lacks permission for this model.",
    429: "LLM rate limit exceeded. Try again shortly.",
}


@router.post("", response_model=ChatResponse, dependencies=[Depends(_chat_limit)])
async def chat(request: ChatRequest, db: Session = Depends(get_db)):
    """Non-streaming RAG chat."""
    context = await _retrieve_async(request.message, db)

    history = [m.model_dump() for m in request.history] if request.history else []
    messages = build_messages(request.message, context, history)

    provider = get_llm_provider()
    # llm_total is observed ONLY on success: time-to-failure samples (an
    # outage returning instant 401s) would collapse the latency percentiles
    # into "faster than ever" during exactly the incident that matters.
    # Failure RATES live in CHAT_REQUESTS, which segments by outcome.
    llm_start = time.perf_counter()
    try:
        response = await provider.generate(messages)
    except HTTPStatusError as exc:
        CHAT_REQUESTS.labels(mode="sync", outcome="llm_error").inc()
        code = exc.response.status_code
        detail = _LLM_ERROR_MAP.get(code, f"LLM provider returned {code}")
        logger.error("LLM HTTP %s: %s", code, detail)
        raise HTTPException(status_code=502, detail=detail) from exc
    except Exception as exc:
        CHAT_REQUESTS.labels(mode="sync", outcome="llm_error").inc()
        logger.exception("LLM generation failed")
        raise HTTPException(status_code=502, detail="LLM generation failed") from exc

    RAG_STAGE_SECONDS.labels(stage="llm_total").observe(time.perf_counter() - llm_start)

    citations = [
        CitationOut(
            source_type=c.source_type,
            source_id=c.source_id,
            snippet=c.snippet,
        )
        for c in verified_citations(response.content, context)
    ]

    CHAT_REQUESTS.labels(mode="sync", outcome="ok").inc()
    return ChatResponse(
        answer=response.content,
        citations=citations,
        model_used=response.model,
        usage=response.usage,
    )


@router.post("/stream", dependencies=[Depends(_chat_limit)])
async def chat_stream(request: ChatRequest, db: Session = Depends(get_db)):
    """Streaming RAG chat via Server-Sent Events."""
    context = await _retrieve_async(request.message, db)

    history = [m.model_dump() for m in request.history] if request.history else []
    messages = build_messages(request.message, context, history)

    provider = get_llm_provider()

    citations = [
        {"source_type": c.source_type, "source_id": c.source_id, "snippet": c.snippet}
        for c in context.citations
    ]

    async def event_generator():
        # Outcome is counted at the serialization point: an LLM failure
        # ships as an {"type": "error"} data event inside an HTTP 200,
        # invisible to status-code-based monitoring. State is initialized
        # BEFORE the first yield and the citations frame sits INSIDE the
        # try — a client that disconnects during the citations send must
        # land in the cancelled bucket, not in no bucket at all.
        outcome = "ok"
        got_first = False
        start = None
        answer: list[str] = []
        try:
            yield f"data: {json.dumps({'type': 'citations', 'data': citations})}\n\n"

            # Timer anchored AFTER the citations frame is drained, so
            # first-token latency measures the provider, not the client's
            # ability to absorb the citations write.
            start = time.perf_counter()
            async for chunk in provider.stream(messages):
                if not got_first:
                    got_first = True
                    RAG_STAGE_SECONDS.labels(stage="llm_first_token").observe(
                        time.perf_counter() - start
                    )
                answer.append(chunk)
                yield f"data: {json.dumps({'type': 'content', 'data': chunk})}\n\n"
            # The answer is complete: replace what was retrieved with what it
            # actually cites (clients take the latest citations frame).
            used = [
                {
                    "source_type": c.source_type,
                    "source_id": c.source_id,
                    "snippet": c.snippet,
                }
                for c in verified_citations("".join(answer), context)
            ]
            yield f"data: {json.dumps({'type': 'citations', 'data': used})}\n\n"
        except HTTPStatusError as exc:
            outcome = "llm_error"
            code = exc.response.status_code
            detail = _LLM_ERROR_MAP.get(code, f"LLM provider returned {code}")
            logger.error("Streaming LLM HTTP %s: %s", code, detail)
            yield f"data: {json.dumps({'type': 'error', 'data': detail})}\n\n"
        except (GeneratorExit, asyncio.CancelledError):
            # Client went away mid-stream — count it distinctly, then let the
            # cancellation propagate.
            outcome = "cancelled"
            raise
        except Exception:
            outcome = "llm_error"
            logger.exception("Streaming LLM error")
            yield f"data: {json.dumps({'type': 'error', 'data': 'LLM generation failed'})}\n\n"
        finally:
            # llm_total only for COMPLETED generations — time-to-failure and
            # truncated-disconnect samples would drag the percentiles down
            # during exactly the incidents they exist to surface. Failure
            # rates live in CHAT_REQUESTS, segmented by outcome.
            if outcome == "ok" and start is not None:
                RAG_STAGE_SECONDS.labels(stage="llm_total").observe(
                    time.perf_counter() - start
                )
            CHAT_REQUESTS.labels(mode="stream", outcome=outcome).inc()

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _llm_status() -> tuple[bool, str]:
    """Whether Scrooge could answer now, without spending anything.

    Ollama is asked (tags are free). OpenAI is not called: a key that is set
    may still be invalid, and the detail says what was actually checked.
    """
    if settings.LLM_PROVIDER == "openai":
        if not settings.OPENAI_API_KEY:
            return False, "CMA_OPENAI_API_KEY is not set"
        return True, "API key configured (not validated)"
    if settings.LLM_PROVIDER != "ollama":
        return False, f"Unknown LLM provider {settings.LLM_PROVIDER!r}"
    try:
        resp = httpx.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=2)
        resp.raise_for_status()
        names = {m.get("name", "") for m in resp.json().get("models") or []}
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, AttributeError, TypeError):
        return False, f"Ollama is not reachable at {settings.OLLAMA_BASE_URL}"
    model = settings.LLM_MODEL
    if model in names or f"{model}:latest" in names:
        return True, "Ollama is reachable and has the model"
    return False, f"Ollama is reachable but has no model {model!r}"


@router.get("/status")
def chat_status():
    """Health check for RAG infrastructure."""
    events_count = 0
    rollups_count = 0
    vector_store = "ready"
    try:
        events_count = get_events_collection().count()
        rollups_count = get_rollups_collection().count()
    except Exception:
        vector_store = "unavailable"
        logger.warning("Could not query vector store counts", exc_info=True)

    llm_available, llm_detail = _llm_status()

    return {
        "vector_store": vector_store,
        "events_indexed": events_count,
        "rollups_indexed": rollups_count,
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": settings.LLM_MODEL,
        "llm_available": llm_available,
        "llm_detail": llm_detail,
        "embedding_model": settings.EMBEDDING_MODEL,
    }
