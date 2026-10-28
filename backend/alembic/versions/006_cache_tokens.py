"""Prompt-cache tokens on events, prompt-cache prices on models

Revision ID: 006
Revises: 005
Create Date: 2026-10-29

ACC-08/09 (decision D3). Anthropic reports cache reads and writes outside
input_tokens and OpenAI bills cached input at a discount; CMA stored neither,
so cached calls were under-counted (Anthropic) or over-priced (OpenAI).

Events gain tokens_cache_read / tokens_cache_write / tokens_cache_write_1h
(parts of tokens_input, which now counts all input tokens). Existing events
get 0: their cache usage was never recorded and cannot be recovered.

Models gain three nullable cache prices. Rows for the seeded Anthropic models
get the official cache prices, but only while their input price is still the
official one (an operator-edited row is left alone). Every other row stays
NULL: cached tokens on such a model are priced at its input price and the
event is marked 'estimated'.
"""

import sqlalchemy as sa

from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None

# model_name: (input price it must still have, cache read, 5m write, 1h write)
# Source: platform.claude.com/docs/en/build-with-claude/prompt-caching (2026-10-29).
_ANTHROPIC_CACHE_PRICES = {
    "claude-opus-4-8": (5.00, 0.50, 6.25, 10.00),
    "claude-opus-4-7": (5.00, 0.50, 6.25, 10.00),
    "claude-sonnet-5": (2.00, 0.20, 2.50, 4.00),
    "claude-sonnet-4-6": (3.00, 0.30, 3.75, 6.00),
    "claude-haiku-4-5": (1.00, 0.10, 1.25, 2.00),
}


def upgrade() -> None:
    with op.batch_alter_table("cost_events") as batch:
        for name in (
            "tokens_cache_read",
            "tokens_cache_write",
            "tokens_cache_write_1h",
        ):
            batch.add_column(
                sa.Column(name, sa.Integer(), nullable=False, server_default="0")
            )
    with op.batch_alter_table("model_pricing") as batch:
        for name in (
            "cache_read_price_per_million",
            "cache_write_price_per_million",
            "cache_write_1h_price_per_million",
        ):
            batch.add_column(sa.Column(name, sa.Float(), nullable=True))

    bind = op.get_bind()
    stmt = sa.text(
        "UPDATE model_pricing SET cache_read_price_per_million = :read, "
        "cache_write_price_per_million = :write, "
        "cache_write_1h_price_per_million = :write_1h "
        "WHERE model_name = :model AND input_price_per_million = :input "
        "AND cache_read_price_per_million IS NULL "
        "AND cache_write_price_per_million IS NULL "
        "AND cache_write_1h_price_per_million IS NULL "
        "AND provider_id IN (SELECT id FROM providers "
        "WHERE lower(provider_type) = 'anthropic')"
    )
    for model, (inp, read, write, write_1h) in _ANTHROPIC_CACHE_PRICES.items():
        bind.execute(
            stmt,
            {
                "model": model,
                "input": inp,
                "read": read,
                "write": write,
                "write_1h": write_1h,
            },
        )


def downgrade() -> None:
    with op.batch_alter_table("model_pricing") as batch:
        batch.drop_column("cache_write_1h_price_per_million")
        batch.drop_column("cache_write_price_per_million")
        batch.drop_column("cache_read_price_per_million")
    with op.batch_alter_table("cost_events") as batch:
        batch.drop_column("tokens_cache_write_1h")
        batch.drop_column("tokens_cache_write")
        batch.drop_column("tokens_cache_read")
