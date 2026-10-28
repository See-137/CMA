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


def test_full_reset_restores_default_pricing(authed_client, db):
    resp = authed_client.post("/api/v1/admin/reset-full", headers=ADMIN)
    assert resp.status_code == 200
    db.expire_all()
    assert db.query(Provider).count() > 0
    assert calculate_cost("openai", "gpt-4o", 1_000_000, 0, db) == 2.5


def test_reembedding_is_chunked(monkeypatch, db):
    """Review M5: one upsert per call failed above Chroma's batch limit."""
    from app.models.models import CostEvent
    from app.services import vector_store
    from app.timeutils import utcnow

    for _ in range(1200):
        db.add(
            CostEvent(
                agent_name="a",
                model_name="m",
                provider_name="p",
                tokens_input=1,
                tokens_output=1,
                cost=0.0,
                timestamp=utcnow(),
            )
        )
    db.commit()
    ids = [e.id for e in db.query(CostEvent.id)]

    batches = []

    class FakeCol:
        def upsert(self, ids, **kwargs):
            batches.append(len(ids))

    monkeypatch.setattr(vector_store, "get_events_collection", lambda: FakeCol())
    monkeypatch.setattr(vector_store, "embed_texts", lambda texts: [[0.0]] * len(texts))
    assert vector_store.embed_and_upsert_events(db, ids) == 1200
    assert max(batches) <= 500 and sum(batches) == 1200


def test_snapshot_fallback_is_marked_estimated(authed_client):
    """A dated snapshot priced via its alias may cost more or less than the
    alias; the cost is stored but flagged as an estimate."""
    _ingest(authed_client, model="gpt-4o-2024-05-13")
    item = authed_client.get("/api/v1/events").json()["items"][0]
    assert item["cost_source"] == "estimated"
    assert item["cost"] == pytest.approx(2.5)


def test_reprice_rejects_inverted_range(authed_client):
    resp = authed_client.post(
        "/api/v1/admin/reprice",
        json={"from_date": "2026-10-02T00:00:00Z", "to_date": "2026-10-01T00:00:00Z"},
        headers=ADMIN,
    )
    assert resp.status_code == 422


def test_reprice_reports_archived_days(authed_client, db):
    from datetime import timedelta

    from app.models.models import CostEvent, DailyCostRollup
    from app.timeutils import utcnow

    old = (utcnow() - timedelta(days=5)).replace(microsecond=0)
    db.add(
        DailyCostRollup(
            date=old.date(),
            agent_name="sdk-bot",
            model_name="gpt-4o",
            provider_name="openai",
            total_cost=0.0,
            request_count=10,
            archived=True,
        )
    )
    db.add(
        CostEvent(
            agent_name="sdk-bot",
            model_name="gpt-4o",
            provider_name="openai",
            tokens_input=1_000_000,
            tokens_output=0,
            cost=0.0,
            timestamp=old,
        )
    )
    db.commit()
    res = authed_client.post("/api/v1/admin/reprice", headers=ADMIN).json()
    assert res["repriced"] == 1
    assert res["archived_dates_not_recomputed"] == [old.date().isoformat()]
    assert res["rollup_dates_recomputed"] == []


def test_interrupted_reprice_leaves_no_stale_rollups(authed_client, db, monkeypatch):
    """Re-review MINOR-1: batches commit as they go, so each batch's days are
    re-rolled immediately; an interruption can't leave a committed day stale."""
    from datetime import timedelta

    from app.models.models import CostEvent, DailyCostRollup
    from app.services import cost_engine
    from app.timeutils import utcnow

    day1 = (utcnow() - timedelta(days=3)).replace(microsecond=0)
    day2 = (utcnow() - timedelta(days=2)).replace(microsecond=0)
    for ts in (day1, day1, day2, day2):
        db.add(
            CostEvent(
                agent_name="a",
                model_name="gpt-4o",
                provider_name="openai",
                tokens_input=1_000_000,
                tokens_output=0,
                cost=0.0,
                timestamp=ts,
            )
        )
    db.commit()
    monkeypatch.setattr(cost_engine, "_REPRICE_BATCH", 2)
    real = cost_engine.cost_for
    calls = {"n": 0}

    def dies_in_second_batch(*a, **k):
        calls["n"] += 1
        if calls["n"] > 2:  # first batch (2 events) completes, then the kill
            raise RuntimeError("worker killed")
        return real(*a, **k)

    monkeypatch.setattr(cost_engine, "cost_for", dies_in_second_batch)
    with pytest.raises(RuntimeError):
        cost_engine.reprice_events(db)
    db.rollback()
    roll = db.query(DailyCostRollup).filter_by(date=day1.date()).one()
    assert roll.total_cost == pytest.approx(5.0)  # first batch's day is current


def test_reprice_rolls_up_a_day_spanning_several_batches(
    authed_client, db, monkeypatch
):
    """Re-review #23: a day whose events spanned batches kept the rollup from
    its first batch only ($5 of $10)."""
    from datetime import timedelta

    from app.models.models import CostEvent, DailyCostRollup
    from app.services import cost_engine
    from app.timeutils import utcnow

    day = (utcnow() - timedelta(days=3)).replace(microsecond=0)
    for _ in range(4):
        db.add(
            CostEvent(
                agent_name="a",
                model_name="gpt-4o",
                provider_name="openai",
                tokens_input=1_000_000,
                tokens_output=0,
                cost=0.0,
                timestamp=day,
            )
        )
    db.commit()
    monkeypatch.setattr(cost_engine, "_REPRICE_BATCH", 2)
    res = cost_engine.reprice_events(db)
    assert res["rollup_dates_recomputed"] == [day.date().isoformat()]
    roll = db.query(DailyCostRollup).filter_by(date=day.date()).one()
    assert roll.total_cost == pytest.approx(10.0)
