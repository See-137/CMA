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
