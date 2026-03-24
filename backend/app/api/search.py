"""Semantic search endpoint — vector similarity + structured filters."""

import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.models import CostEvent
from app.schemas.schemas import (
    EventOut,
    SemanticSearchRequest,
    SemanticSearchResponse,
    SemanticSearchResult,
)
from app.services.vector_store import embed_text, search_events

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["search"])


@router.post("/semantic", response_model=SemanticSearchResponse)
def semantic_search(request: SemanticSearchRequest, db: Session = Depends(get_db)):
    """Semantic search over cost events using vector similarity."""
    # 1. Embed query
    t0 = time.perf_counter()
    query_embedding = embed_text(request.query)
    embed_ms = (time.perf_counter() - t0) * 1000

    # 2. Build ChromaDB where filter from structured params
    where_clauses = []
    if request.agent_name:
        where_clauses.append({"agent_name": request.agent_name})
    if request.provider:
        where_clauses.append({"provider_name": request.provider})
    if request.model:
        where_clauses.append({"model_name": request.model})
    if request.status:
        where_clauses.append({"status": request.status})

    where = None
    if len(where_clauses) == 1:
        where = where_clauses[0]
    elif len(where_clauses) > 1:
        where = {"$and": where_clauses}

    # 3. Search ChromaDB (over-fetch 2x for date filtering headroom)
    t1 = time.perf_counter()
    fetch_count = request.top_k * 2
    raw_results = search_events(query_embedding, n_results=fetch_count, where=where)
    search_ms = (time.perf_counter() - t1) * 1000

    # 4. Post-filter by date range
    filtered = []
    for r in raw_results:
        ts_str = r.get("metadata", {}).get("timestamp", "")
        if ts_str and (request.from_date or request.to_date):
            try:
                ts = datetime.fromisoformat(ts_str)
                if request.from_date and ts < request.from_date:
                    continue
                if request.to_date and ts > request.to_date:
                    continue
            except ValueError:
                pass
        filtered.append(r)

    # 5. Fetch full CostEvent objects from DB
    event_ids = [int(r["id"]) for r in filtered[: request.top_k]]
    if not event_ids:
        return SemanticSearchResponse(
            results=[],
            query_embedding_time_ms=round(embed_ms, 2),
            search_time_ms=round(search_ms, 2),
        )

    events_by_id = {
        e.id: e for e in db.query(CostEvent).filter(CostEvent.id.in_(event_ids)).all()
    }

    # 6. Build ranked results
    results = []
    for r in filtered[: request.top_k]:
        eid = int(r["id"])
        event = events_by_id.get(eid)
        if event is None:
            continue

        # Cosine distance → similarity score (0-100%)
        distance = r.get("distance", 0)
        similarity = round((1 - distance) * 100, 1)

        results.append(
            SemanticSearchResult(
                event=EventOut.model_validate(event),
                similarity_score=similarity,
                highlight=r.get("document", "")[:200],
            )
        )

    return SemanticSearchResponse(
        results=results,
        query_embedding_time_ms=round(embed_ms, 2),
        search_time_ms=round(search_ms, 2),
    )
