"""Idempotency key on cost events

Revision ID: 008
Revises: 007
Create Date: 2026-11-04

ACC-10. A timeout after the server committed made the SDK resend, storing the
call twice. Events may now carry a client-generated event_id, unique when
present. Existing rows have none and keep working.
"""

import sqlalchemy as sa

from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cost_events") as batch:
        batch.add_column(sa.Column("event_id", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_cost_events_event_id", ["event_id"])


def downgrade() -> None:
    with op.batch_alter_table("cost_events") as batch:
        batch.drop_constraint("uq_cost_events_event_id", type_="unique")
        batch.drop_column("event_id")
