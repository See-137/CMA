"""Golden questions over a fixed ledger: what Scrooge is handed must be right.

Deterministic (no LLM): each question names the window label and exact total the
data block has to carry, or the marker it must raise. ``scripts/scrooge_eval.py``
runs the same set through a real model.
"""

import pytest

from app.services import rag_engine as rag
from tests.conftest import TestSession
from tests.golden_ledger import GOLDEN, seed_ledger


@pytest.fixture
def db(authed_client):
    s = TestSession()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def no_vectors(monkeypatch):
    monkeypatch.setattr(rag, "embed_text", lambda q: [0.0] * 384)
    monkeypatch.setattr(rag, "search_events", lambda *a, **k: [])
    monkeypatch.setattr(rag, "search_rollups", lambda *a, **k: [])


@pytest.mark.parametrize("case", GOLDEN, ids=[c["q"] for c in GOLDEN])
def test_golden_question_gets_the_right_data(db, case):
    seed_ledger(db)
    context = rag.retrieve(case["q"], db)
    block = rag._data_block(context)
    totals = [r for r in context.sql_results if r.get("type") == "total"]
    if "total" in case:
        assert totals, block
        assert totals[0]["total_cost"] == pytest.approx(case["total"]), block
    if "requests" in case:
        assert totals[0]["request_count"] == case["requests"], block
    for text in case.get("contains", []):
        assert text in block, block
    for text in case.get("excludes", []):
        assert text not in block, block
