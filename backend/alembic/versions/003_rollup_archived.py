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

    # Existing installs: decide per rollup day whether its raw events were
    # already pruned (by the pre-003 retention, which never marked anything).
    # Rollups are only ever created from events, so a day whose rollups count
    # MORE requests than the events still stored lost events to pruning —
    # whether or not late events arrived since. Such a day must be archived,
    # or the next prune would recompute it from the survivors and silently
    # drop the pruned spend.
    conn = op.get_bind()
    days = conn.execute(
        sa.text("SELECT date, SUM(request_count) FROM daily_cost_rollups GROUP BY date")
    ).all()
    for day, rolled_up in days:
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
        if (rolled_up or 0) > (remaining or 0):
            conn.execute(
                sa.text("UPDATE daily_cost_rollups SET archived = :t WHERE date = :d"),
                {"t": True, "d": day},
            )


def downgrade() -> None:
    with op.batch_alter_table("daily_cost_rollups") as batch:
        batch.drop_column("archived")
