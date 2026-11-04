"""Current models price at the vendors' rates, on fresh installs and upgrades."""

import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text

from app.models.models import Alert, CostEvent, DailyCostRollup, ModelPricing
from app.seed import SEED_DATA
from app.services import rag_engine as rag
from app.services.budget_checker import check_unpriced_models
from app.services.cost_engine import cost_for, reprice_events, resolve_pricing_match
from app.timeutils import utcnow
from tests.conftest import TEST_ENGINE, TestSession
from tests.test_migrations import _alembic


@pytest.fixture
def db():
    session = TestSession()
    yield session
    session.close()


@pytest.fixture
def admin_headers():
    return {"X-Admin-Password": "testpass123"}


_MIGRATION = Path(__file__).parent.parent / "alembic/versions/007_current_pricing.py"


def _migration():
    spec = importlib.util.spec_from_file_location("m007", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(client, **kw):
    body = {"agent_name": "a", "tokens_input": 1000, "tokens_output": 100, **kw}
    resp = client.post("/api/v1/events", json=body)
    assert resp.status_code in (200, 201), resp.text


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        ("anthropic", "claude-opus-5-5", 6.00),
        ("anthropic", "claude-sonnet-5-5", 3.00),
        ("anthropic", "claude-fable-5-1", 15.00),
        ("anthropic", "claude-haiku-4-5-20251001", 1.50),  # dated id, still exact
        ("google", "gemini-4-argon", 3.00),
    ],
)
def test_current_models_are_priced(db, provider, model, expected):
    pricing, exact = resolve_pricing_match(provider, model, db)
    assert exact
    assert cost_for(pricing, 1_000_000, 100_000) == pytest.approx(expected)


def _row(db, name):
    return db.query(ModelPricing).filter(ModelPricing.model_name == name).one()


def test_long_context_bills_the_whole_request_above_the_threshold(db):
    haiku = _row(db, "claude-haiku-5-5")
    at = cost_for(haiku, 100_000, 1000)
    assert at == pytest.approx((100_000 * 0.10 + 1000 * 0.50) / 1e6)
    above = cost_for(haiku, 100_001, 1000)
    assert above == pytest.approx((100_001 * 0.50 + 1000 * 2.50) / 1e6)


def test_long_context_multiplies_cache_rates_and_output_separately(db):
    sol = _row(db, "gpt-6.1-sol")
    cost = cost_for(sol, 300_000, 1000, cache_read=200_000, cache_write=50_000)
    expected = (
        50_000 * 2.0 * 2 + 200_000 * 0.10 * 2 + 50_000 * 2.50 * 2 + 1000 * 10.0 * 1.5
    ) / 1e6
    assert cost == pytest.approx(expected)


def test_long_context_settings_must_come_together(authed_client, db):
    provider_id = _row(db, "gpt-6.1-sol").provider_id
    resp = authed_client.post(
        f"/api/v1/providers/{provider_id}/models",
        json={
            "model_name": "half",
            "input_price_per_million": 1,
            "output_price_per_million": 1,
            "long_context_threshold": 1000,
        },
    )
    assert resp.status_code == 422
    model_id = _row(db, "gpt-6.1-sol").id
    resp = authed_client.put(
        f"/api/v1/models/{model_id}", json={"long_context_threshold": None}
    )
    assert resp.status_code == 422
    db.expire_all()
    assert _row(db, "gpt-6.1-sol").long_context_threshold == 272_000
    resp = authed_client.put(
        f"/api/v1/models/{model_id}",
        json={
            "long_context_threshold": None,
            "long_context_input_multiplier": None,
            "long_context_output_multiplier": None,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["long_context_threshold"] is None


def test_migration_list_matches_the_seed():
    """A model added to one list and not the other reaches only some installs."""
    current = {}
    for provider_type, models in _migration().CURRENT_MODELS.items():
        for model, alias, inp, out, cache, long_context in models:
            current[(provider_type, model)] = (alias, inp, out, cache, long_context)
    legacy = {
        "gpt-4o",
        "gpt-4-turbo",
        "gpt-4",
        "gpt-3.5-turbo",
        "claude-3.5-sonnet",
        "claude-3-opus",
        "claude-3-sonnet",
        "claude-3-haiku",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    }
    seeded = {}
    for provider in SEED_DATA:
        for m in provider["models"]:
            if m["model_name"] not in legacy:
                seeded[(provider["provider_type"], m["model_name"])] = (
                    m.get("alias"),
                    m["input"],
                    m["output"],
                    m.get("cache"),
                    m.get("long"),
                )
    assert seeded == current


def test_007_adds_missing_models_and_leaves_operator_rows_alone(tmp_path):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    _alembic(url, "upgrade", "006")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO providers (id, name, provider_type, is_active) VALUES "
                "(1,'Anthropic','anthropic',1),(2,'OpenAI','openai',1),"
                "(3,'Old Google','google',0)"
            )
        )
        c.execute(
            text(
                "INSERT INTO model_pricing (provider_id, model_name, "
                "input_price_per_million, output_price_per_million, is_active) VALUES "
                "(1,'claude-sonnet-5',3.0,15.0,1),"  # old seed: corrected
                "(1,'claude-opus-5-5',99.0,99.0,1),"  # operator's: untouched
                "(1,'claude-haiku-4-5',1.0,5.0,1)"
            )
        )
    eng.dispose()
    _alembic(url, "upgrade", "007")
    eng = create_engine(url)
    with eng.connect() as c:
        rows = {
            r["model_name"]: r
            for r in c.execute(text("SELECT * FROM model_pricing")).mappings().all()
        }
    assert rows["claude-sonnet-5"]["input_price_per_million"] == 2.0
    assert rows["claude-sonnet-5"]["output_price_per_million"] == 10.0
    assert rows["claude-opus-5-5"]["input_price_per_million"] == 99.0
    assert rows["claude-opus-5-5"]["long_context_threshold"] is None
    assert rows["claude-haiku-5-5"]["long_context_threshold"] == 100_000
    assert rows["gpt-6.1-sol"]["long_context_output_multiplier"] == 1.5
    assert rows["claude-haiku-4-5"]["model_alias"] == "claude-haiku-4-5-20251001"
    # A provider the operator removed stays without new rows.
    assert "gemini-4-argon" not in rows
    eng.dispose()
    _alembic(url, "downgrade", "006")
    eng = create_engine(url)
    with eng.connect() as c:
        kept = c.execute(
            text("SELECT count(*) FROM model_pricing WHERE model_name='gpt-6.1-sol'")
        ).scalar()
    assert kept == 1
    eng.dispose()
    _alembic(url, "upgrade", "007")


def test_007_does_not_correct_an_edited_sonnet_5(tmp_path):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    _alembic(url, "upgrade", "006")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO providers (id, name, provider_type, is_active) "
                "VALUES (1,'Anthropic','anthropic',1)"
            )
        )
        c.execute(
            text(
                "INSERT INTO model_pricing (provider_id, model_name, "
                "input_price_per_million, output_price_per_million, is_active) "
                "VALUES (1,'claude-sonnet-5',3.5,15.0,1)"
            )
        )
    eng.dispose()
    _alembic(url, "upgrade", "007")
    eng = create_engine(url)
    with eng.connect() as c:
        price = c.execute(
            text(
                "SELECT input_price_per_million FROM model_pricing "
                "WHERE model_name='claude-sonnet-5'"
            )
        ).scalar()
    assert price == 3.5
    eng.dispose()


def test_reprice_include_priced_applies_a_corrected_price(
    authed_client, db, admin_headers
):
    _event(authed_client, provider="anthropic", model="claude-sonnet-5")
    _event(authed_client, provider="anthropic", model="claude-sonnet-5", cost=7.0)
    row = _row(db, "claude-sonnet-5")
    before = 1000 * row.input_price_per_million + 100 * row.output_price_per_million
    authed_client.put(
        f"/api/v1/models/{row.id}", json={"input_price_per_million": 20.0}
    )
    plain = authed_client.post(
        "/api/v1/admin/reprice", json={}, headers=admin_headers
    ).json()
    assert plain["repriced"] == 0
    full = authed_client.post(
        "/api/v1/admin/reprice", json={"include_priced": True}, headers=admin_headers
    ).json()
    assert full["repriced"] == 1
    after = 1000 * 20.0 + 100 * row.output_price_per_million
    assert full["added_cost"] == pytest.approx((after - before) / 1e6)
    costs = sorted(
        e["cost"] for e in authed_client.get("/api/v1/events").json()["items"]
    )
    assert costs == [pytest.approx(after / 1e6), 7.0]  # client cost untouched


def test_reprice_include_priced_keeps_cost_when_the_price_is_gone(
    authed_client, db, admin_headers
):
    _event(authed_client, provider="anthropic", model="claude-sonnet-5")
    row = _row(db, "claude-sonnet-5")
    authed_client.delete(f"/api/v1/models/{row.id}")
    result = authed_client.post(
        "/api/v1/admin/reprice", json={"include_priced": True}, headers=admin_headers
    ).json()
    assert result["repriced"] == 0
    assert result["still_unpriced"] == {}
    item = authed_client.get("/api/v1/events").json()["items"][0]
    assert item["cost"] > 0
    assert item["cost_source"] == "pricing"


def test_budget_check_and_list_report_unpriced_spend(authed_client):
    authed_client.post(
        "/api/v1/budgets",
        json={
            "name": "b",
            "scope": "global",
            "period": "daily",
            "limit_amount": 1,
            "control_action": "block",
        },
    )
    assert authed_client.get("/api/v1/budgets/check").json()["unpriced_events"] == 0
    _event(authed_client, provider="openai", model="no-such-model")
    body = authed_client.get("/api/v1/budgets/check").json()
    assert body["allowed"] is True
    assert body["unpriced_events"] == 1
    assert authed_client.get("/api/v1/budgets").json()[0]["unpriced_events"] == 1


def test_first_event_of_an_unpriced_model_raises_one_alert(
    authed_client, db, monkeypatch
):
    # The background task opens its own session on the app's engine.
    monkeypatch.setattr("app.database.SessionLocal", TestSession)
    _event(authed_client, provider="openai", model="no-such-model")
    _event(authed_client, provider="openai", model="no-such-model")
    alerts = db.query(Alert).filter(Alert.alert_type == "unpriced_model").all()
    assert len(alerts) == 1
    assert "openai/no-such-model" in alerts[0].message


def test_unpriced_alerts_are_capped_per_day(db):
    pairs = {("openai", f"invented-{i}") for i in range(50)}
    assert len(check_unpriced_models(db, pairs)) == 20
    assert check_unpriced_models(db, {("openai", "one-more")}) == []


def _spend(db, model="gpt-4o", n=1, tokens_in=5000, tokens_out=500, **kw):
    """Events stored directly, with a cost that is not what the table says."""
    for _ in range(n):
        db.add(
            CostEvent(
                agent_name="a",
                model_name=model,
                provider_name="openai",
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                cost=kw.get("cost", 1.0),
                cost_source=kw.get("cost_source", "pricing"),
                timestamp=kw.get("timestamp", utcnow() - timedelta(hours=1)),
            )
        )
    db.commit()


def _alternatives(db):
    rows = rag._run_comparison_query(rag.QueryIntent(needs_comparison=True), db)
    return {a["model"]: a["estimated_cost"] for a in rows[0]["alternatives"]}


def test_what_if_applies_the_threshold_per_call_not_to_the_total(db, monkeypatch):
    """1000 short calls are not one long prompt."""
    _spend(db, n=1000)
    assert _alternatives(db)["gpt-6-luna"] == pytest.approx(
        1000 * (5000 * 0.10 + 500 * 0.50) / 1e6
    )


def test_what_if_charges_the_long_rate_on_the_calls_above_it(db):
    _spend(db, n=10)
    _spend(db, n=1, tokens_in=300_000, tokens_out=1000)
    short = 10 * (5000 * 0.10 + 500 * 0.50)
    long = 300_000 * 0.10 * 2 + 1000 * 0.50 * 1.5
    assert _alternatives(db)["gpt-6-luna"] == pytest.approx((short + long) / 1e6)


def test_downgrade_and_upgrade_restores_long_context_settings(tmp_path):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    _alembic(url, "upgrade", "006")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO providers (id, name, provider_type, is_active) "
                "VALUES (1,'OpenAI','openai',1)"
            )
        )
    eng.dispose()
    _alembic(url, "upgrade", "007")
    _alembic(url, "downgrade", "006")
    _alembic(url, "upgrade", "007")
    eng = create_engine(url)
    with eng.connect() as c:
        threshold = c.execute(
            text(
                "SELECT long_context_threshold FROM model_pricing "
                "WHERE model_name='gpt-6.1-sol'"
            )
        ).scalar()
    eng.dispose()
    assert threshold == 272_000


def test_operator_alias_for_a_new_model_is_respected(tmp_path):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    _alembic(url, "upgrade", "006")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO providers (id, name, provider_type, is_active) "
                "VALUES (1,'Anthropic','anthropic',1)"
            )
        )
        c.execute(
            text(
                "INSERT INTO model_pricing (provider_id, model_name, model_alias, "
                "input_price_per_million, output_price_per_million, is_active) "
                "VALUES (1,'my-haiku','x, claude-haiku-5-5',9.0,9.0,1)"
            )
        )
    eng.dispose()
    _alembic(url, "upgrade", "007")
    eng = create_engine(url)
    with eng.connect() as c:
        count = c.execute(
            text(
                "SELECT count(*) FROM model_pricing WHERE model_name='claude-haiku-5-5'"
            )
        ).scalar()
    eng.dispose()
    assert count == 0


@pytest.mark.parametrize(
    "row",
    [
        ("3.0", "16.0", "NULL"),  # output edited
        ("3.0", "15.0", "0.5"),  # cache price set
    ],
)
def test_007_only_corrects_an_entirely_untouched_sonnet_5(tmp_path, row):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    _alembic(url, "upgrade", "006")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO providers (id, name, provider_type, is_active) "
                "VALUES (1,'Anthropic','anthropic',1)"
            )
        )
        c.execute(
            text(
                "INSERT INTO model_pricing (provider_id, model_name, "
                "input_price_per_million, output_price_per_million, "
                f"cache_read_price_per_million, is_active) VALUES "
                f"(1,'claude-sonnet-5',{row[0]},{row[1]},{row[2]},1)"
            )
        )
    eng.dispose()
    _alembic(url, "upgrade", "007")
    eng = create_engine(url)
    with eng.connect() as c:
        price = c.execute(
            text(
                "SELECT input_price_per_million FROM model_pricing "
                "WHERE model_name='claude-sonnet-5'"
            )
        ).scalar()
    eng.dispose()
    assert price == 3.0


def test_reprice_all_skips_unchanged_and_reports_only_changes(
    authed_client, db, admin_headers
):
    _event(authed_client, provider="anthropic", model="claude-sonnet-5")
    result = authed_client.post(
        "/api/v1/admin/reprice", json={"include_priced": True}, headers=admin_headers
    ).json()
    assert result["repriced"] == 0
    assert result["added_cost"] == 0


def test_reprice_all_covers_estimated_events_and_date_limits(
    authed_client, db, admin_headers
):
    old = utcnow() - timedelta(days=5)
    recent = utcnow() - timedelta(days=2)
    _spend(db, cost=9.0, cost_source="estimated", timestamp=old)
    _spend(db, cost=9.0, cost_source="pricing", timestamp=recent)
    limited = authed_client.post(
        "/api/v1/admin/reprice",
        json={
            "include_priced": True,
            "from_date": (recent - timedelta(hours=1)).isoformat() + "Z",
        },
        headers=admin_headers,
    ).json()
    assert limited["repriced"] == 1
    everything = authed_client.post(
        "/api/v1/admin/reprice", json={"include_priced": True}, headers=admin_headers
    ).json()
    assert everything["repriced"] == 1  # the older, estimated one


def test_reprice_all_recomputes_rollups_of_changed_days_only(db):
    day = (utcnow() - timedelta(days=3)).replace(microsecond=0)
    other = (utcnow() - timedelta(days=4)).replace(microsecond=0)
    _spend(db, cost=9.0, timestamp=day, tokens_in=1_000_000, tokens_out=0)
    _spend(
        db,
        cost=2.5,  # already right at gpt-4o's $2.50
        timestamp=other,
        tokens_in=1_000_000,
        tokens_out=0,
    )
    result = reprice_events(db, include_priced=True)
    assert result["rollup_dates_recomputed"] == [day.date().isoformat()]
    roll = db.query(DailyCostRollup).filter_by(date=day.date()).one()
    assert roll.total_cost == pytest.approx(2.5)


def test_reprice_rolls_each_day_once(db, monkeypatch):
    from app.services import cost_engine, rollup

    day = (utcnow() - timedelta(days=3)).replace(microsecond=0)
    _spend(db, n=5, cost=9.0, timestamp=day)
    monkeypatch.setattr(cost_engine, "_REPRICE_BATCH", 2)
    calls = []
    real = rollup.compute_daily_rollups
    monkeypatch.setattr(
        rollup, "compute_daily_rollups", lambda d, t: calls.append(t) or real(d, t)
    )
    assert reprice_events(db, include_priced=True)["repriced"] == 5
    assert len(calls) == 1


def _block(authed_client, **kw):
    body = {
        "name": "b",
        "scope": "global",
        "period": "daily",
        "limit_amount": 1000,
        "control_action": "block",
        **kw,
    }
    assert authed_client.post("/api/v1/budgets", json=body).status_code == 201


def test_unpriced_count_ignores_priced_events(authed_client):
    _block(authed_client)
    _event(authed_client, provider="openai", model="gpt-4o")
    assert authed_client.get("/api/v1/budgets/check").json()["unpriced_events"] == 0


def test_unpriced_count_follows_scope_and_blocking(authed_client):
    _block(authed_client, scope="agent", scope_ref="other")
    _block(authed_client, name="soft", control_action="alert")
    _event(authed_client, provider="openai", model="no-such-model")
    # A global alert-only budget and another agent's budget: no warning here.
    resp = authed_client.get("/api/v1/budgets/check", params={"agent_name": "a"})
    assert resp.json()["unpriced_events"] == 0


def test_precall_check_runs_one_query_per_budget(authed_client, db):
    _block(authed_client)
    _event(authed_client, provider="openai", model="no-such-model")
    statements = []

    def count(_conn, _cur, statement, *_):
        if "cost_events" in statement:
            statements.append(statement)

    event.listen(TEST_ENGINE, "before_cursor_execute", count)
    try:
        authed_client.get("/api/v1/budgets/check")
    finally:
        event.remove(TEST_ENGINE, "before_cursor_execute", count)
    assert len(statements) == 1


def test_a_failed_budget_check_does_not_lose_the_unpriced_alert(
    authed_client, db, monkeypatch
):
    from app.api import events as events_api

    monkeypatch.setattr("app.database.SessionLocal", TestSession)

    def boom(_db):
        raise RuntimeError("locked")

    monkeypatch.setattr(events_api, "check_budgets", boom)
    _event(authed_client, provider="openai", model="no-such-model")
    assert db.query(Alert).filter(Alert.alert_type == "unpriced_model").count() == 1


def test_alert_text_cannot_ping_slack_and_provider_spelling_is_one_alert(db):
    created = check_unpriced_models(
        db, {("OpenAI", "<!channel> x"), ("openai", "<!channel> x")}
    )
    assert len(created) == 1
    assert "<" not in created[0].message
    assert "openai/" in created[0].message


def test_threshold_has_an_upper_bound(authed_client, db):
    model_id = _row(db, "gpt-6.1-sol").id
    resp = authed_client.put(
        f"/api/v1/models/{model_id}", json={"long_context_threshold": 2**40}
    )
    assert resp.status_code == 422
