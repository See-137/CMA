"""ChromaDB vector store + sentence-transformers embedding service."""

import logging
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.timeutils import utcnow

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy singletons — initialised on first use, cached for process lifetime
# ---------------------------------------------------------------------------

_chroma_client = None
_embedding_model = None


def get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        import chromadb

        logger.info("Initialising ChromaDB at %s", settings.CHROMA_PERSIST_DIR)
        _chroma_client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
    return _chroma_client


def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model: %s", settings.EMBEDDING_MODEL)
        _embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL)
        logger.info("Embedding model ready")
    return _embedding_model


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------


def get_events_collection():
    return get_chroma_client().get_or_create_collection(
        name="cost_events",
        metadata={"hnsw:space": "cosine"},
    )


def get_rollups_collection():
    return get_chroma_client().get_or_create_collection(
        name="daily_rollups",
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------


def embed_text(text: str) -> list[float]:
    return get_embedding_model().encode(text).tolist()


def embed_texts(texts: list[str]) -> list[list[float]]:
    return get_embedding_model().encode(texts).tolist()


# ---------------------------------------------------------------------------
# Text templates — turn structured rows into natural language for embedding
# ---------------------------------------------------------------------------


def _event_to_text(row) -> str:
    """Build an embedding-friendly sentence from a CostEvent (or dict)."""
    if isinstance(row, dict):
        g = row.get
    else:
        g = lambda k, d=None: getattr(row, k, d)

    return (
        f"Agent '{g('agent_name')}' called {g('model_name')} via {g('provider_name')}. "
        f"Status: {g('status', 'success')}. "
        f"Tokens: {g('tokens_input', 0)} in, {g('tokens_output', 0)} out. "
        f"Cost: ${g('cost', 0):.6f}. "
        f"Duration: {g('duration_ms', 0)}ms. "
        f"Workflow: {g('workflow') or 'none'}. "
        f"Swarm: {g('swarm') or 'none'}."
    )


def _rollup_to_text(row) -> str:
    if isinstance(row, dict):
        g = row.get
    else:
        g = lambda k, d=None: getattr(row, k, d)

    return (
        f"Daily summary for {g('date')} (UTC day): "
        f"Agent '{g('agent_name')}' using {g('model_name')} via {g('provider_name')}. "
        f"Total cost: ${g('total_cost', 0):.4f}. "
        f"Requests: {g('request_count', 0)} ({g('failure_count', 0)} failures). "
        f"Tokens: {g('total_tokens_input', 0)} in, {g('total_tokens_output', 0)} out. "
        f"Avg duration: {g('avg_duration_ms', 0) or 0:.0f}ms."
    )


# ---------------------------------------------------------------------------
# Metadata builders — stored alongside vectors for ChromaDB `where` filters
# ---------------------------------------------------------------------------


def _epoch(value: datetime) -> int:
    """Seconds since 1970 of a naive-UTC time: Chroma filters compare numbers."""
    return int(value.replace(tzinfo=timezone.utc).timestamp())


def _event_metadata(row) -> dict:
    if isinstance(row, dict):
        g = row.get
    else:
        g = lambda k, d=None: getattr(row, k, d)

    ts = g("timestamp")
    epoch = _epoch(ts) if isinstance(ts, datetime) else 0
    if isinstance(ts, datetime):
        ts = ts.isoformat()

    return {
        "epoch": epoch,
        "agent_name": g("agent_name") or "",
        "model_name": g("model_name") or "",
        "provider_name": g("provider_name") or "",
        "status": g("status", "success") or "success",
        "cost": float(g("cost", 0) or 0),
        "timestamp": ts or "",
        "workflow": g("workflow") or "",
        "swarm": g("swarm") or "",
    }


def _rollup_metadata(row) -> dict:
    if isinstance(row, dict):
        g = row.get
    else:
        g = lambda k, d=None: getattr(row, k, d)

    d = g("date")
    epoch = _epoch(datetime.fromisoformat(d.isoformat())) if isinstance(d, date) else 0
    if isinstance(d, date):
        d = d.isoformat()

    return {
        "epoch": epoch,
        "agent_name": g("agent_name") or "",
        "model_name": g("model_name") or "",
        "provider_name": g("provider_name") or "",
        "date": d or "",
        "total_cost": float(g("total_cost", 0) or 0),
        "request_count": int(g("request_count", 0) or 0),
    }


# ---------------------------------------------------------------------------
# Upsert / delete
# ---------------------------------------------------------------------------


def delete_events(event_ids: list[int]) -> None:
    """Best-effort batch removal of event embeddings (called on retention prune)."""
    if not event_ids:
        return
    try:
        get_events_collection().delete(ids=[str(i) for i in event_ids])
    except Exception:
        logger.warning(
            "Failed to delete %d event embeddings from vector store",
            len(event_ids),
            exc_info=True,
        )


def clear_vector_store() -> None:
    """Delete all documents from both ChromaDB collections (called on DB reset)."""
    client = get_chroma_client()
    for name in ("cost_events", "daily_rollups"):
        try:
            client.delete_collection(name)
            logger.info("Cleared ChromaDB collection: %s", name)
        except Exception:
            logger.debug("Collection %s did not exist, skipping", name, exc_info=True)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search_events(
    query_embedding: list[float],
    n_results: int = 10,
    where: dict | None = None,
) -> list[dict]:
    col = get_events_collection()
    if col.count() == 0:
        return []

    kwargs: dict = {
        "query_embeddings": [query_embedding],
        "n_results": min(n_results, col.count()),
    }
    if where:
        kwargs["where"] = where

    results = col.query(**kwargs)
    return _unpack_chroma_results(results)


def search_rollups(
    query_embedding: list[float],
    n_results: int = 10,
    where: dict | None = None,
) -> list[dict]:
    col = get_rollups_collection()
    if col.count() == 0:
        return []

    kwargs: dict = {
        "query_embeddings": [query_embedding],
        "n_results": min(n_results, col.count()),
    }
    if where:
        kwargs["where"] = where
    return _unpack_chroma_results(col.query(**kwargs))


def backfill_vector_epochs(batch_size: int = 1000) -> int:
    """Give records stored before RAG-10 their ``epoch``, without re-embedding.

    Time-filtered searches cannot see a record with no epoch, so until this has
    run they only miss older examples: totals come from SQL either way. Runs in
    daily maintenance; records already done cost one read.
    """
    updated = 0
    for col, stamp in (
        (get_events_collection(), "timestamp"),
        (get_rollups_collection(), "date"),
    ):
        offset = 0
        while True:
            page = col.get(include=["metadatas"], limit=batch_size, offset=offset)
            ids = page.get("ids") or []
            if not ids:
                break
            offset += len(ids)
            todo = []
            for record_id, meta in zip(ids, page.get("metadatas") or []):
                meta = meta or {}
                if "epoch" in meta:
                    continue
                try:
                    when = datetime.fromisoformat(str(meta.get(stamp) or ""))
                except ValueError:
                    continue
                todo.append((record_id, {**meta, "epoch": _epoch(when)}))
            if todo:
                col.update(ids=[t[0] for t in todo], metadatas=[t[1] for t in todo])
                updated += len(todo)
    return updated


def _unpack_chroma_results(results: dict) -> list[dict]:
    """Flatten ChromaDB query results into a list of dicts."""
    items = []
    ids = results.get("ids", [[]])[0]
    distances = results.get("distances", [[]])[0]
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    for i, doc_id in enumerate(ids):
        items.append(
            {
                "id": doc_id,
                "distance": distances[i] if i < len(distances) else None,
                "document": documents[i] if i < len(documents) else None,
                "metadata": metadatas[i] if i < len(metadatas) else {},
            }
        )
    return items


# ---------------------------------------------------------------------------
# Batch helpers — used by ingestion hook and backfill
# ---------------------------------------------------------------------------


_EMBED_CHUNK = 500  # well under Chroma's max batch (5461 in 1.5.x)


def embed_and_upsert_events(db: Session, event_ids: list[int]) -> int:
    """Embed CostEvent rows by ID, in chunks (a single upsert above Chroma's
    max batch size is rejected outright — e.g. re-embedding after a large
    reprice). Returns count embedded."""
    from app.models.models import CostEvent

    col = get_events_collection()
    total = 0
    for i in range(0, len(event_ids), _EMBED_CHUNK):
        chunk = event_ids[i : i + _EMBED_CHUNK]
        events = db.query(CostEvent).filter(CostEvent.id.in_(chunk)).all()
        if not events:
            continue
        texts = [_event_to_text(e) for e in events]
        col.upsert(
            ids=[str(e.id) for e in events],
            documents=texts,
            embeddings=embed_texts(texts),
            metadatas=[_event_metadata(e) for e in events],
        )
        total += len(events)
    return total


def backfill_event_embeddings(db: Session, batch_size: int = 500) -> int:
    """Embed any CostEvents not yet in ChromaDB. Returns total embedded."""
    from app.models.models import CostEvent

    col = get_events_collection()
    existing_ids = set(col.get()["ids"]) if col.count() > 0 else set()

    total_embedded = 0
    offset = 0

    while True:
        rows = (
            db.query(CostEvent)
            .order_by(CostEvent.id)
            .offset(offset)
            .limit(batch_size)
            .all()
        )
        if not rows:
            break

        to_embed = [r for r in rows if str(r.id) not in existing_ids]
        if to_embed:
            texts = [_event_to_text(e) for e in to_embed]
            embeddings = embed_texts(texts)
            col.upsert(
                ids=[str(e.id) for e in to_embed],
                documents=texts,
                embeddings=embeddings,
                metadatas=[_event_metadata(e) for e in to_embed],
            )
            total_embedded += len(to_embed)

        offset += batch_size

    if total_embedded:
        logger.info("Backfilled %d event embeddings", total_embedded)
    return total_embedded


def embed_rollups_for_date(db: Session, target_date: date | None = None) -> int:
    """Embed DailyCostRollup rows for a given date (default: yesterday)."""
    from datetime import timedelta

    from app.models.models import DailyCostRollup

    if target_date is None:
        target_date = utcnow().date() - timedelta(days=1)

    rollups = (
        db.query(DailyCostRollup).filter(DailyCostRollup.date == target_date).all()
    )
    if not rollups:
        return 0

    texts = [_rollup_to_text(r) for r in rollups]
    embeddings = embed_texts(texts)

    col = get_rollups_collection()
    col.upsert(
        ids=[str(r.id) for r in rollups],
        documents=texts,
        embeddings=embeddings,
        metadatas=[_rollup_metadata(r) for r in rollups],
    )
    logger.info("Embedded %d rollups for %s", len(rollups), target_date)
    return len(rollups)
