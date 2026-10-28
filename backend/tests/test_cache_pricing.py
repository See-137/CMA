"""Prompt-cache accounting (ACC-08/09, decision D3).

tokens_input counts ALL input tokens; tokens_cache_read / tokens_cache_write
(/ tokens_cache_write_1h) say how many of them hit or filled a prompt cache,
and are priced at the model's cache prices. A missing cache price falls back
to the input price (5-minute write price for 1-hour writes) and the event is
marked 'estimated'.
"""

from datetime import timedelta

import pytest

from app.models.models import CostEvent, ModelPricing, Provider
from app.services import rag_engine as rag
from app.services.cost_engine import cache_prices_exact, cost_for
from app.timeutils import utcnow
from tests.conftest import TestSession

ADMIN = {"X-Admin-Password": "testpass123"}


@pytest.fixture
def db():
    s = TestSession()
    try:
        yield s
    finally:
        s.close()


def _post(client, **fields):
    ev = {"agent_name": "bot", "tokens_output": 0, **fields}
    return client.post("/api/v1/events", json=ev)


def _only_event(client):
    items = client.get("/api/v1/events").json()["items"]
    assert len(items) == 1
    return items[0]


def _model(db, name):
    return db.query(ModelPricing).filter(ModelPricing.model_name == name).one()


def test_seeded_anthropic_cache_prices(db):
    m = _model(db, "claude-sonnet-4-6")
    assert (
        m.cache_read_price_per_million,
        m.cache_write_price_per_million,
        m.cache_write_1h_price_per_million,
    ) == (0.30, 3.75, 6.00)
    # Official base price (checked 2026-10-29), not the old $3/$15.
    s5 = _model(db, "claude-sonnet-5")
    assert (s5.input_price_per_million, s5.output_price_per_million) == (2.0, 10.0)


def test_acc08_anthropic_cache_tokens_priced(authed_client):
    """The FINDINGS ACC-08 repro: input 20 + cache write 500 + cache read
    50000 used to be stored (and billed) as 20 input tokens."""
    r = _post(
        authed_client,
        model="claude-sonnet-4-6",
        provider="anthropic",
        tokens_input=20 + 500 + 50_000,
        tokens_cache_read=50_000,
        tokens_cache_write=500,
    )
    assert r.status_code == 200, r.text
    ev = _only_event(authed_client)
    assert ev["tokens_input"] == 50_520
    assert (ev["tokens_cache_read"], ev["tokens_cache_write"]) == (50_000, 500)
    # 20 x $3 + 50,000 x $0.30 + 500 x $3.75 per million
    assert ev["cost"] == pytest.approx((20 * 3 + 50_000 * 0.30 + 500 * 3.75) / 1e6)
    assert ev["cost_source"] == "pricing"


def test_one_hour_cache_writes_use_their_price(authed_client):
    _post(
        authed_client,
        model="claude-sonnet-4-6",
        provider="anthropic",
        tokens_input=1_000,
        tokens_cache_write=1_000,
        tokens_cache_write_1h=400,
    )
    ev = _only_event(authed_client)
    assert ev["tokens_cache_write_1h"] == 400
    assert ev["cost"] == pytest.approx((600 * 3.75 + 400 * 6.0) / 1e6)
    assert ev["cost_source"] == "pricing"


def test_acc09_openai_cached_input(authed_client, db):
    """No cache price on gpt-4o: the cached part is priced at the input
    price (old behaviour) and flagged 'estimated'. With a cached-input price
    set, the discount applies and the cost is exact again."""
    _post(
        authed_client,
        model="gpt-4o",
        provider="openai",
        tokens_input=10_000,
        tokens_cache_read=9_000,
    )
    first = _only_event(authed_client)
    assert first["cost"] == pytest.approx(10_000 * 2.5 / 1e6)
    assert first["cost_source"] == "estimated"

    model_id = _model(db, "gpt-4o").id
    r = authed_client.put(
        f"/api/v1/models/{model_id}", json={"cache_read_price_per_million": 1.25}
    )
    assert r.status_code == 200
    assert r.json()["cache_read_price_per_million"] == 1.25
    _post(
        authed_client,
        model="gpt-4o",
        provider="openai",
        tokens_input=10_000,
        tokens_cache_read=9_000,
    )
    second = authed_client.get("/api/v1/events").json()["items"][0]
    assert second["cost"] == pytest.approx((1_000 * 2.5 + 9_000 * 1.25) / 1e6)
    assert second["cost_source"] == "pricing"


def test_events_without_cache_fields_are_unchanged(authed_client):
    _post(
        authed_client,
        model="claude-sonnet-4-6",
        provider="anthropic",
        tokens_input=1_000_000,
        tokens_output=1_000_000,
    )
    ev = _only_event(authed_client)
    assert ev["cost"] == pytest.approx(3.0 + 15.0)
    assert ev["cost_source"] == "pricing"
    assert (ev["tokens_cache_read"], ev["tokens_cache_write"]) == (0, 0)


@pytest.mark.parametrize(
    "fields",
    [
        {"tokens_input": 100, "tokens_cache_read": 80, "tokens_cache_write": 30},
        {"tokens_input": 100, "tokens_cache_write": 10, "tokens_cache_write_1h": 11},
        {"tokens_input": 100, "tokens_cache_read": -1},
    ],
)
def test_inconsistent_cache_counts_are_rejected(authed_client, fields):
    r = _post(authed_client, model="gpt-4o", provider="openai", **fields)
    assert r.status_code == 422


def test_reprice_applies_cache_prices(authed_client, db):
    """An unpriced event with cache tokens is repriced with cache prices."""
    _post(
        authed_client,
        model="claude-new",
        provider="anthropic",
        tokens_input=1_000_000,
        tokens_cache_read=900_000,
    )
    assert _only_event(authed_client)["cost_source"] == "unpriced"
    anthropic = db.query(Provider).filter(Provider.name == "Anthropic").one()
    r = authed_client.post(
        f"/api/v1/providers/{anthropic.id}/models",
        json={
            "model_name": "claude-new",
            "input_price_per_million": 2.0,
            "output_price_per_million": 10.0,
            "cache_read_price_per_million": 0.2,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["cache_read_price_per_million"] == 0.2
    out = authed_client.post("/api/v1/admin/reprice", json={}, headers=ADMIN).json()
    assert out["repriced"] == 1
    ev = _only_event(authed_client)
    assert ev["cost"] == pytest.approx(100_000 * 2.0 / 1e6 + 900_000 * 0.2 / 1e6)
    assert ev["cost_source"] == "pricing"


def test_model_update_null_clears_cache_price_but_not_required_prices(
    authed_client, db
):
    model_id = _model(db, "claude-haiku-4-5").id
    r = authed_client.put(
        f"/api/v1/models/{model_id}",
        json={"cache_read_price_per_million": None, "input_price_per_million": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cache_read_price_per_million"] is None
    assert body["input_price_per_million"] == 1.0  # null ignored, no 500


def test_cost_for_fallbacks():
    m = ModelPricing(
        input_price_per_million=4.0,
        output_price_per_million=0.0,
        cache_write_price_per_million=5.0,
    )
    # 1-hour writes with no 1-hour price fall back to the 5-minute price;
    # reads with no read price fall back to the input price.
    assert cost_for(m, 300, 0, 100, 200, 50) == pytest.approx(
        (0 * 4 + 100 * 4 + 150 * 5 + 50 * 5) / 1e6
    )
    assert not cache_prices_exact(m, 100, 0, 0)
    assert not cache_prices_exact(m, 0, 200, 50)
    assert cache_prices_exact(m, 0, 200, 0)
    assert cache_prices_exact(m)  # no cache tokens: nothing to estimate


def test_scrooge_what_if_uses_alternative_cache_prices(db):
    """The comparison prices the same tokens AND cache usage on each
    alternative model, instead of all input at the full input price."""
    db.add(
        CostEvent(
            agent_name="bot",
            model_name="claude-sonnet-4-6",
            provider_name="anthropic",
            tokens_input=1_000_000,
            tokens_output=0,
            tokens_cache_read=1_000_000,
            cost=0.30,
            cost_source="pricing",
            timestamp=utcnow() - timedelta(hours=1),
        )
    )
    db.commit()
    rows = rag._run_comparison_query(rag.QueryIntent(needs_comparison=True), db)
    alts = {a["model"]: a for a in rows[0]["alternatives"]}
    # Sonnet 5's cache read is $0.20/M: 1M cached tokens cost $0.20, not $2.
    assert alts["claude-sonnet-5"]["estimated_cost"] == pytest.approx(0.20)
