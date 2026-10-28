"""Pricing resolution: the strings real instrumentation sends must price.

Regression: the SDK reports provider="openai"/"anthropic" but seeded providers
are named "OpenAI"/"Anthropic"; the exact-match lookup priced every
auto-instrumented call at $0.
"""

import pytest

from app.models.models import ModelPricing, Provider
from app.services.cost_engine import calculate_cost, resolve_pricing
from tests.conftest import TestSession


@pytest.fixture
def db():
    s = TestSession()
    try:
        yield s
    finally:
        s.close()


@pytest.mark.parametrize(
    "provider,model,expected",
    [
        ("OpenAI", "gpt-4o", 2.50),  # exact (pre-existing behaviour)
        ("openai", "gpt-4o", 2.50),  # SDK spelling
        ("OPENAI", "GPT-4o", 2.50),  # case-insensitive model fallback
        ("openai", "gpt-4o-2024-08-06", 2.50),  # dated snapshot
        ("anthropic", "claude-sonnet-4-6", 3.00),
        ("anthropic", "claude-3-haiku-20240307", 0.25),  # compact date suffix
    ],
)
def test_sdk_spellings_resolve_to_seeded_prices(db, provider, model, expected):
    cost = calculate_cost(provider, model, 1_000_000, 0, db)
    assert cost == pytest.approx(expected)


def test_unknown_model_is_zero(db):
    assert calculate_cost("openai", "no-such-model", 1_000_000, 0, db) == 0.0
    assert resolve_pricing("nobody", "gpt-4o", db) is None


def test_alias_list_matches(db):
    p = db.query(Provider).filter(Provider.name == "Anthropic").one()
    db.add(
        ModelPricing(
            provider_id=p.id,
            model_name="claude-3.5-sonnet-v2",
            model_alias="claude-3-5-sonnet-latest, Claude-3-5-Sonnet-Custom",
            input_price_per_million=3.0,
            output_price_per_million=15.0,
        )
    )
    db.commit()
    hit = resolve_pricing("anthropic", "claude-3-5-sonnet-custom", db)
    assert hit is not None and hit.model_name == "claude-3.5-sonnet-v2"


def test_exact_model_beats_snapshot_fallback(db):
    p = db.query(Provider).filter(Provider.name == "OpenAI").one()
    db.add(
        ModelPricing(
            provider_id=p.id,
            model_name="gpt-4o-2024-05-13",
            input_price_per_million=5.0,
            output_price_per_million=15.0,
        )
    )
    db.commit()
    assert calculate_cost("openai", "gpt-4o-2024-05-13", 1_000_000, 0, db) == 5.0
    assert calculate_cost("openai", "gpt-4o-2024-08-06", 1_000_000, 0, db) == 2.5


def test_exact_provider_name_beats_type_match(db):
    # A second provider of the same vendor type with different pricing.
    azure = Provider(name="Azure East", provider_type="openai")
    db.add(azure)
    db.flush()
    db.add(
        ModelPricing(
            provider_id=azure.id,
            model_name="gpt-4o",
            input_price_per_million=99.0,
            output_price_per_million=99.0,
        )
    )
    db.commit()
    assert calculate_cost("openai", "gpt-4o", 1_000_000, 0, db) == 2.5
    assert calculate_cost("Azure East", "gpt-4o", 1_000_000, 0, db) == 99.0


def test_inactive_pricing_is_ignored(db):
    m = (
        db.query(ModelPricing)
        .join(Provider)
        .filter(Provider.name == "OpenAI", ModelPricing.model_name == "gpt-4o")
        .one()
    )
    m.is_active = False
    db.commit()
    assert calculate_cost("openai", "gpt-4o", 1_000_000, 0, db) == 0.0


def test_ingested_sdk_event_is_priced(authed_client):
    resp = authed_client.post(
        "/api/v1/events",
        json={
            "agent_name": "sdk-bot",
            "model": "gpt-4o",
            "provider": "openai",
            "tokens_input": 1_000_000,
            "tokens_output": 100_000,
        },
    )
    assert resp.json()["processed"] == 1
    item = authed_client.get("/api/v1/events").json()["items"][0]
    assert item["cost"] == pytest.approx(2.5 + 1.0)


# -- cost_source + repricing ---------------------------------------------------

ADMIN = {"X-Admin-Password": "testpass123"}


def _ingest(client, **overrides):
    ev = {
        "agent_name": "sdk-bot",
        "model": "gpt-4o",
        "provider": "openai",
        "tokens_input": 1_000_000,
        "tokens_output": 0,
    }
    ev.update(overrides)
    assert client.post("/api/v1/events", json=ev).json()["processed"] == 1


def test_cost_source_is_recorded(authed_client):
    _ingest(authed_client)
    _ingest(authed_client, model="mystery-model")
    _ingest(authed_client, cost=1.23)
    items = authed_client.get("/api/v1/events").json()["items"]
    sources = sorted(i["cost_source"] for i in items)
    assert sources == ["client", "pricing", "unpriced"]


def test_dashboard_overview_counts_unpriced(authed_client):
    _ingest(authed_client)
    _ingest(authed_client, model="mystery-model")
    body = authed_client.get("/api/v1/dashboard/overview").json()
    assert body["unpriced_events"] == 1


def test_reprice_requires_admin(authed_client):
    assert authed_client.post("/api/v1/admin/reprice").status_code in (401, 403)


def test_reprice_fixes_unpriced_and_legacy_rows(authed_client, db):
    from datetime import timedelta

    from app.models.models import CostEvent, DailyCostRollup
    from app.services.rollup import compute_daily_rollups
    from app.timeutils import utcnow

    yesterday = (utcnow() - timedelta(days=1)).replace(microsecond=0)
    # Legacy row shaped like the old exact-match bug: $0, NULL cost_source.
    db.add(
        CostEvent(
            agent_name="sdk-bot",
            model_name="gpt-4o",
            provider_name="openai",
            tokens_input=1_000_000,
            tokens_output=0,
            cost=0.0,
            timestamp=yesterday,
        )
    )
    # A client-supplied $0 must never be touched (cost_source='client').
    db.add(
        CostEvent(
            agent_name="sdk-bot",
            model_name="gpt-4o",
            provider_name="openai",
            tokens_input=1_000_000,
            tokens_output=0,
            cost=0.0,
            cost_source="client",
            timestamp=yesterday,
        )
    )
    db.commit()
    compute_daily_rollups(db, yesterday.date())
    _ingest(authed_client, model="later-priced")  # unpriced today

    dry = authed_client.post(
        "/api/v1/admin/reprice", json={"dry_run": True}, headers=ADMIN
    ).json()
    assert dry["repriced"] == 1
    assert dry["still_unpriced"] == {"openai/later-priced": 1}
    assert db.query(CostEvent).filter(CostEvent.cost > 0).count() == 0

    # Add pricing for the unknown model, then reprice for real.
    openai = db.query(Provider).filter(Provider.name == "OpenAI").one()
    db.add(
        ModelPricing(
            provider_id=openai.id,
            model_name="later-priced",
            input_price_per_million=1.0,
            output_price_per_million=1.0,
        )
    )
    db.commit()
    res = authed_client.post("/api/v1/admin/reprice", headers=ADMIN).json()
    assert res["repriced"] == 2
    assert res["added_cost"] == pytest.approx(3.5)
    assert res["still_unpriced"] == {}
    assert res["rollup_dates_recomputed"] == [yesterday.date().isoformat()]

    db.expire_all()
    roll = db.query(DailyCostRollup).filter_by(date=yesterday.date()).one()
    assert roll.total_cost == pytest.approx(2.5)  # client $0 row unchanged
    assert db.query(CostEvent).filter(CostEvent.cost_source == "client").one().cost == 0
    # Idempotent: nothing left to reprice.
    again = authed_client.post("/api/v1/admin/reprice", headers=ADMIN).json()
    assert again["repriced"] == 0
