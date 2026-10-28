"""One threshold alert per budget, threshold and period

Revision ID: 005
Revises: 004
Create Date: 2026-10-28

Threshold alerts were de-duplicated by looking for an earlier alert whose
message contained "(threshold: N%)". Two ingestion requests evaluating the
same budget at once both found none and both inserted. The alert now records
its threshold and period start, and a unique constraint makes the database
reject the second insert. Pre-existing alerts keep NULLs (NULLs never
collide); their messages still count for de-duplication.
"""

import sqlalchemy as sa

from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("alerts") as batch:
        batch.add_column(sa.Column("threshold", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("period_start", sa.DateTime(), nullable=True))
        batch.create_unique_constraint(
            "uq_alerts_budget_threshold_period",
            ["budget_id", "threshold", "period_start"],
        )


def downgrade() -> None:
    with op.batch_alter_table("alerts") as batch:
        batch.drop_constraint("uq_alerts_budget_threshold_period", type_="unique")
        batch.drop_column("period_start")
        batch.drop_column("threshold")
