"""Migration data-safety tests (run against a throwaway SQLite file)."""

import os
import pathlib
import subprocess
import sys

from sqlalchemy import create_engine, text

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]


def _alembic(url, *args):
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        check=True,
        cwd=BACKEND_DIR,
        env={**os.environ, "CMA_DATABASE_URL": url},
        capture_output=True,
    )


def test_003_archives_days_already_pruned_even_with_late_events(tmp_path):
    """Review M2: a day pruned before 003 that has since received a late event
    must be archived, or the next prune recomputes it from the late event and
    drops the pruned spend."""
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    _alembic(url, "upgrade", "002")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO daily_cost_rollups (date, agent_name, model_name, "
                "provider_name, total_cost, total_tokens_input, total_tokens_output, "
                "request_count, failure_count) VALUES "
                # pruned, no late events
                "('2026-01-01','a','m','p',100,0,0,50,0),"
                # pruned, then one late event arrived
                "('2026-01-02','a','m','p',100,0,0,50,0),"
                # never pruned: rollup matches its events
                "('2026-10-01','a','m','p',5,0,0,1,0),"
                # never pruned: a late event arrived after rollup
                "('2026-10-02','a','m','p',5,0,0,1,0)"
            )
        )
        c.execute(
            text(
                "INSERT INTO cost_events (agent_name, model_name, provider_name, "
                "tokens_input, tokens_output, cost, timestamp) VALUES "
                "('a','m','p',0,0,2,'2026-01-02 09:00:00'),"
                "('a','m','p',0,0,5,'2026-10-01 12:00:00'),"
                "('a','m','p',0,0,5,'2026-10-02 12:00:00'),"
                "('a','m','p',0,0,1,'2026-10-02 13:00:00')"
            )
        )
    eng.dispose()
    _alembic(url, "upgrade", "head")
    eng = create_engine(url)
    with eng.connect() as c:
        archived = dict(
            c.execute(text("SELECT date, archived FROM daily_cost_rollups")).all()
        )
    assert archived == {
        "2026-01-01": 1,
        "2026-01-02": 1,
        "2026-10-01": 0,
        "2026-10-02": 0,
    }
    _alembic(url, "downgrade", "base")
    _alembic(url, "upgrade", "head")


def test_005_keeps_existing_alerts_and_rejects_duplicates(tmp_path):
    """BUD-06: existing alerts survive with NULL keys (which never collide);
    a second alert for the same budget/threshold/period is rejected."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    url = f"sqlite:///{tmp_path / 'mig.db'}"
    _alembic(url, "upgrade", "004")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO budgets (name, scope, period, limit_amount, is_active) "
                "VALUES ('b','global','daily',1,1)"
            )
        )
        for _ in range(2):  # legacy duplicates already present
            c.execute(
                text(
                    "INSERT INTO alerts (budget_id, alert_type, message, severity) "
                    "VALUES (1,'budget_exceeded','x (threshold: 100%)','critical')"
                )
            )
    eng.dispose()
    _alembic(url, "upgrade", "head")
    eng = create_engine(url)
    insert = text(
        "INSERT INTO alerts (budget_id, alert_type, message, threshold, period_start) "
        "VALUES (1,'budget_exceeded','y',100,'2026-10-28 00:00:00')"
    )
    with eng.begin() as c:
        assert c.execute(text("SELECT COUNT(*) FROM alerts")).scalar() == 2
        c.execute(insert)
    with pytest.raises(IntegrityError), eng.begin() as c:
        c.execute(insert)
    eng.dispose()
    _alembic(url, "downgrade", "004")
    eng = create_engine(url)
    with eng.connect() as c:
        cols = {r[1] for r in c.execute(text("PRAGMA table_info(alerts)"))}
        assert {"threshold", "period_start"}.isdisjoint(cols)
        assert c.execute(text("SELECT COUNT(*) FROM alerts")).scalar() == 3
    eng.dispose()
    _alembic(url, "upgrade", "head")
