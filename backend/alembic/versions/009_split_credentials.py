"""Ingest keys, admin sessions, one setup row

Revision ID: 009
Revises: 008
Create Date: 2026-11-05

SEC-01 / decision D4. One shared key used to be both the SDK credential and the
management credential. Ingest keys and admin sessions replace it. An existing
install's key keeps full access for 90 days, then becomes ingest-only, so nothing
breaks on upgrade day. A new install has no such key.

SEC-03: `singleton` makes a second setup row impossible. If a past race left
several rows, the first (the one every query reads) keeps it.
"""

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingest_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("key_hash", name="uq_ingest_keys_key_hash"),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_admin_sessions_token_hash"),
    )
    with op.batch_alter_table("setup_config") as batch:
        batch.add_column(sa.Column("singleton", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "legacy_key_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("legacy_full_until", sa.DateTime(), nullable=True))
        batch.create_unique_constraint("uq_setup_config_singleton", ["singleton"])

    bind = op.get_bind()
    until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=90)
    bind.execute(
        sa.text(
            "UPDATE setup_config SET legacy_key_enabled = :yes, "
            "legacy_full_until = :until"
        ),
        {"yes": True, "until": until},
    )
    first = bind.execute(sa.text("SELECT min(id) FROM setup_config")).scalar()
    if first is not None:
        bind.execute(
            sa.text("UPDATE setup_config SET singleton = 1 WHERE id = :id"),
            {"id": first},
        )


def downgrade() -> None:
    with op.batch_alter_table("setup_config") as batch:
        batch.drop_constraint("uq_setup_config_singleton", type_="unique")
        batch.drop_column("legacy_full_until")
        batch.drop_column("legacy_key_enabled")
        batch.drop_column("singleton")
    op.drop_table("admin_sessions")
    op.drop_table("ingest_keys")
