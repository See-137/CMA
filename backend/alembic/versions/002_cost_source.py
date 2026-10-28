"""Record how each event's cost was determined

Revision ID: 002
Revises: 001
Create Date: 2026-10-28

cost_source: 'pricing' (looked up in model_pricing), 'estimated' (matched
only by stripping a dated snapshot suffix), 'client' (supplied by the
caller), 'unpriced' (no price row matched, stored as 0.0). Pre-existing
rows stay NULL (unknown); the reprice endpoint treats NULL rows with cost 0
and non-zero tokens as candidates.
"""

import sqlalchemy as sa

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cost_events") as batch:
        batch.add_column(sa.Column("cost_source", sa.String(20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cost_events") as batch:
        batch.drop_column("cost_source")
