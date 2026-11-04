"""Citations say what the answer rests on; vector records stay inside the window."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api import chat as chat_api
from app.models.models import CostEvent
from app.services import rag_engine as rag
from app.services import vector_store as vs
from app.services.rag_engine import Citation, RetrievalContext, verified_citations
from app.timeutils import utcnow
from tests.conftest import TestSession


def _context():
    return RetrievalContext(
        citations=[
            Citation("sql_query", snippet="totals", labels=("total", "breakdown")),
            Citation("sql_query", snippet="timeline", labels=("timeline",)),
            Citation("event", "7", "record one", labels=("1",)),
            Citation("rollup", "9", "record two", labels=("2",)),
        ]
    )


def test_only_cited_sources_are_returned():
    used = verified_citations("You spent $5 [total], mostly here [2].", _context())
    assert [c.snippet for c in used] == ["totals", "record two"]


def test_a_label_the_data_never_had_is_flagged():
    used = verified_citations("$5 [total], see [9] and [made_up].", _context())
    unverified = [c.snippet for c in used if c.source_type == "unverified"]
    assert len(unverified) == 2
    assert "[9]" in unverified[0]
    assert "[made_up]" in unverified[1]


@pytest.mark.parametrize(
    "answer",
    ["No labels at all.", "A [link](https://x.example)", "Agent [not a label] here"],
)
def test_text_that_is_not_a_citation_is_ignored(answer):
    assert verified_citations(answer, _context()) == []


def test_each_label_is_listed_once():
    used = verified_citations("[total] [total] [breakdown]", _context())
    assert len(used) == 1


class _Provider:
    def __init__(self, answer):
        self.answer = answer

    async def generate(self, messages):
        return SimpleNamespace(content=self.answer, model="fake", usage={})

    async def stream(self, messages):
        for piece in (self.answer[:5], self.answer[5:]):
            yield piece


@pytest.fixture
def no_vectors(monkeypatch):
    monkeypatch.setattr(rag, "embed_text", lambda q: [0.0] * 384)
    monkeypatch.setattr(rag, "search_events", lambda *a, **k: [])
    monkeypatch.setattr(rag, "search_rollups", lambda *a, **k: [])


def test_chat_returns_verified_citations(authed_client, monkeypatch, no_vectors):
    monkeypatch.setattr(
        chat_api, "get_llm_provider", lambda: _Provider("$0 [total] and [42]")
    )
    body = authed_client.post(
        "/api/v1/chat", json={"message": "total spend today"}
    ).json()
    kinds = [c["source_type"] for c in body["citations"]]
    assert kinds == ["sql_query", "unverified"]


def test_stream_ends_with_the_verified_citations(
    authed_client, monkeypatch, no_vectors
):
    monkeypatch.setattr(
        chat_api, "get_llm_provider", lambda: _Provider("no labels here")
    )
    resp = authed_client.post(
        "/api/v1/chat/stream", json={"message": "total spend today"}
    )
    frames = [
        json.loads(line[6:])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    citations = [f for f in frames if f["type"] == "citations"]
    assert len(citations) == 2
    assert citations[0]["data"]  # what was retrieved, while the answer streams
    assert citations[1]["data"] == []  # what it ended up citing
    assert frames[-1]["type"] == "done"


@pytest.fixture
def chroma(tmp_path, monkeypatch):
    """A real Chroma store with hand-made embeddings."""
    import chromadb

    monkeypatch.setattr(
        vs, "_chroma_client", chromadb.PersistentClient(path=str(tmp_path))
    )
    monkeypatch.setattr(vs, "embed_text", lambda text: [1.0, 0.0, 0.0])
    monkeypatch.setattr(rag, "embed_text", lambda text: [1.0, 0.0, 0.0])
    monkeypatch.setattr(
        vs, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
    )


def _store(db, days_ago):
    event = CostEvent(
        agent_name=f"agent{days_ago}",
        model_name="m",
        provider_name="openai",
        tokens_input=1,
        tokens_output=1,
        cost=1.0,
        timestamp=utcnow() - timedelta(days=days_ago),
    )
    db.add(event)
    db.commit()
    vs.embed_and_upsert_events(db, [event.id])


def test_a_dated_question_only_retrieves_records_from_its_window(authed_client, chroma):
    with TestSession() as db:
        _store(db, 1)
        _store(db, 30)
        context = rag.retrieve("Why did spend change yesterday?", db)
        names = [r["metadata"]["agent_name"] for r in context.vector_results]
        assert names == ["agent1"]
        undated = rag.retrieve("Why did spend change?", db)
        assert len(undated.vector_results) == 2


def test_data_block_says_when_a_record_happened(authed_client, chroma):
    with TestSession() as db:
        _store(db, 1)
        block = rag._data_block(rag.retrieve("why did spend change yesterday?", db))
    assert "(at 20" in block


def test_older_records_get_an_epoch_without_being_re_embedded(chroma):
    events = vs.get_events_collection()
    rollups = vs.get_rollups_collection()
    events.upsert(
        ids=["1", "2"],
        embeddings=[[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        documents=["old", "no time"],
        metadatas=[{"timestamp": "2026-01-02T03:04:05"}, {"agent_name": "x"}],
    )
    rollups.upsert(
        ids=["r1"],
        embeddings=[[1.0, 0.0, 0.0]],
        documents=["day"],
        metadatas=[{"date": "2026-01-02"}],
    )
    assert vs.backfill_vector_epochs() == 2
    assert vs.backfill_vector_epochs() == 0
    meta = events.get(ids=["1"])["metadatas"][0]
    assert meta["epoch"] == vs._epoch(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
    assert rollups.get(ids=["r1"])["metadatas"][0]["epoch"] == vs._epoch(
        datetime(2026, 1, 2, tzinfo=UTC)
    )
