"""RAG chat endpoints — non-streaming and SSE streaming."""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from httpx import HTTPStatusError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings
from app.schemas.schemas import ChatRequest, ChatResponse, CitationOut
from app.services.llm_provider import get_llm_provider
from app.services.rag_engine import build_messages, retrieve
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
    try:
        response = await provider.generate(messages)
    except HTTPStatusError as exc:
        code = exc.response.status_code
        detail = _LLM_ERROR_MAP.get(code, f"LLM provider returned {code}")
        logger.error("LLM HTTP %s: %s", code, detail)
        raise HTTPException(status_code=502, detail=detail) from exc
    except Exception as exc:
        logger.exception("LLM generation failed")
        raise HTTPException(status_code=502, detail="LLM generation failed") from exc

    citations = [
        CitationOut(
            source_type=c.source_type,
            source_id=c.source_id,
            snippet=c.snippet,
        )
        for c in context.citations
    ]

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
        # Send citations first
        yield f"data: {json.dumps({'type': 'citations', 'data': citations})}\n\n"

        # Stream LLM response
        try:
            async for chunk in provider.stream(messages):
                yield f"data: {json.dumps({'type': 'content', 'data': chunk})}\n\n"
        except HTTPStatusError as exc:
            code = exc.response.status_code
            detail = _LLM_ERROR_MAP.get(code, f"LLM provider returned {code}")
            logger.error("Streaming LLM HTTP %s: %s", code, detail)
            yield f"data: {json.dumps({'type': 'error', 'data': detail})}\n\n"
        except Exception:
            logger.exception("Streaming LLM error")
            yield f"data: {json.dumps({'type': 'error', 'data': 'LLM generation failed'})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/status")
def chat_status():
    """Health check for RAG infrastructure."""
    events_count = 0
    rollups_count = 0
    try:
        events_count = get_events_collection().count()
        rollups_count = get_rollups_collection().count()
    except Exception:
        logger.warning("Could not query vector store counts")

    llm_available = True
    if settings.LLM_PROVIDER == "openai" and not settings.OPENAI_API_KEY:
        llm_available = False

    return {
        "vector_store": "ready",
        "events_indexed": events_count,
        "rollups_indexed": rollups_count,
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": settings.LLM_MODEL,
        "llm_available": llm_available,
        "embedding_model": settings.EMBEDDING_MODEL,
    }
