"""Flag events recorded while a blocking budget was already exhausted

Revision ID: 004
Revises: 003
Create Date: 2026-10-28

Budget enforcement moved from ingestion (where 'stop' discarded spend that
had already happened) to a pre-call check. Every event is now stored; an
event that arrives while a matching blocking budget is at/over its limit is
flagged so the leak past enforcement stays visible.
"""

import sqlalchemy as sa

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cost_events") as batch:
        batch.add_column(
            sa.Column(
                "over_budget", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("cost_events") as batch:
        batch.drop_column("over_budget")
