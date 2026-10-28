"""Mark daily rollups whose raw events have been pruned

Revision ID: 003
Revises: 002
Create Date: 2026-10-28

Once retention deletes a day's events, that day's rollup rows are the only
record of its spend. ``archived`` rows must never be recomputed from the
(partial) events that remain; late events for such a day are added to them.
"""

from datetime import date, datetime, timedelta

import sqlalchemy as sa

from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("daily_cost_rollups") as batch:
        batch.add_column(
            sa.Column(
                "archived", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )

    # Existing installs: a rollup day with no remaining raw events was already
    # pruned (rollups are only ever created from events), so it is archived.
    conn = op.get_bind()
    days = [
        r[0]
        for r in conn.execute(sa.text("SELECT DISTINCT date FROM daily_cost_rollups"))
    ]
    for day in days:
        if isinstance(day, str):
            day = date.fromisoformat(day)
        start = datetime.combine(day, datetime.min.time())
        remaining = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM cost_events "
                "WHERE timestamp >= :start AND timestamp < :end"
            ),
            {"start": start, "end": start + timedelta(days=1)},
        ).scalar()
        if not remaining:
            conn.execute(
                sa.text("UPDATE daily_cost_rollups SET archived = :t WHERE date = :d"),
                {"t": True, "d": day},
            )


def downgrade() -> None:
    with op.batch_alter_table("daily_cost_rollups") as batch:
        batch.drop_column("archived")
