"""Long-context pricing on models; current models for existing installs

Revision ID: 007
Revises: 006
Create Date: 2026-11-04

ACC-22/23/20. Vendors shipped models CMA had no row for, so their calls were
stored at $0 and budgets never counted them. Seeding runs only on an empty
table, so existing installs need this.

- model_pricing gains long_context_threshold and two multipliers (a prompt over
  the threshold is billed at multiplied rates for the whole request).
- Missing current models are inserted, under the first active provider of the
  right type. Existing rows are never overwritten, and a provider type the
  operator removed stays removed.
- One correction: claude-sonnet-5 moves from the old seeded $3/$15 to $2/$10
  only if it still has exactly those prices (ACC-20). Stored event costs do not
  change; Settings > Reprice with "include priced" applies it to history.

The list is frozen here on purpose; tests compare it with app/seed.py.
Downgrade drops the columns and keeps the inserted rows, which stay valid.
"""

from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None

_OPENAI_LONG = (272_000, 2.0, 1.5)

# provider_type: (model, alias, input, output, cache (read, write, long write), long)
# Checked against the vendors' official pricing pages on 2026-11-04.
CURRENT_MODELS = {
    "openai": [
        ("gpt-6-astra", None, 10.00, 50.00, (1.00, 12.50, 25.00), _OPENAI_LONG),
        ("gpt-6.1-sol", None, 2.00, 10.00, (0.10, 2.50, 5.00), _OPENAI_LONG),
        ("gpt-6.1-terra", None, 1.00, 5.00, (0.05, 1.25, 2.50), _OPENAI_LONG),
        ("gpt-6-luna", None, 0.10, 0.50, (0.01, 0.125, 0.25), _OPENAI_LONG),
        ("chat-latest", None, 5.00, 30.00, (0.50, None, None), None),
        ("gpt-5.3-codex", None, 1.75, 14.00, (0.175, None, None), None),
        ("gpt-5.6-sol", "gpt-5.6", 4.00, 20.00, (0.40, 5.00, 10.00), _OPENAI_LONG),
        ("gpt-5.6-cyber", None, 12.50, 75.00, (1.25, 15.625, 31.25), None),
        ("gpt-6-rosalind", None, 15.00, 75.00, (1.50, 18.75, 37.50), None),
    ],
    "anthropic": [
        ("claude-fable-5-1", None, 10.00, 50.00, (0.25, 12.50, 20.00), None),
        ("claude-opus-5-5", None, 4.00, 20.00, (0.20, 5.00, 8.00), None),
        ("claude-sonnet-5-5", None, 2.00, 10.00, (0.10, 2.50, 4.00), None),
        (
            "claude-haiku-5-5",
            None,
            0.10,
            0.50,
            (0.01, 0.125, 0.20),
            (100_000, 5.0, 5.0),
        ),
        ("claude-fable-5", None, 10.00, 50.00, (1.00, 12.50, 20.00), None),
        ("claude-opus-5", None, 5.00, 25.00, (0.50, 6.25, 10.00), None),
        ("claude-opus-4-8", None, 5.00, 25.00, (0.50, 6.25, 10.00), None),
        ("claude-opus-4-7", None, 5.00, 25.00, (0.50, 6.25, 10.00), None),
        ("claude-opus-4-6", None, 5.00, 25.00, (0.50, 6.25, 10.00), None),
        (
            "claude-opus-4-5",
            "claude-opus-4-5-20251101",
            5.00,
            25.00,
            (0.50, 6.25, 10.00),
            None,
        ),
        ("claude-sonnet-5", None, 2.00, 10.00, (0.20, 2.50, 4.00), None),
        ("claude-sonnet-4-6", None, 3.00, 15.00, (0.30, 3.75, 6.00), None),
        (
            "claude-sonnet-4-5",
            "claude-sonnet-4-5-20250929",
            3.00,
            15.00,
            (0.30, 3.75, 6.00),
            None,
        ),
        (
            "claude-haiku-4-5",
            "claude-haiku-4-5-20251001",
            1.00,
            5.00,
            (0.10, 1.25, 2.00),
            None,
        ),
        ("claude-mythos-5-1", None, 10.00, 50.00, (0.25, 12.50, 20.00), None),
        ("claude-mythos-5", None, 10.00, 50.00, (1.00, 12.50, 20.00), None),
        (
            "claude-opus-4-1",
            "claude-opus-4-1-20250805",
            15.00,
            75.00,
            (1.50, 18.75, 30.00),
            None,
        ),
        (
            "claude-opus-4",
            "claude-opus-4-20250514",
            15.00,
            75.00,
            (1.50, 18.75, 30.00),
            None,
        ),
        (
            "claude-sonnet-4",
            "claude-sonnet-4-20250514",
            3.00,
            15.00,
            (0.30, 3.75, 6.00),
            None,
        ),
        (
            "claude-3-5-haiku",
            "claude-3-5-haiku-20241022",
            0.80,
            4.00,
            (0.08, 1.00, 1.60),
            None,
        ),
    ],
    "google": [
        ("gemini-4-argon", None, 2.00, 10.00, (0.10, None, None), None),
        ("gemini-3.8-flash", None, 0.75, 3.75, (0.075, None, None), None),
        ("gemini-3.6-flash", None, 0.75, 3.75, (0.075, None, None), None),
        ("gemini-3.5-flash-lite", None, 0.30, 2.50, (0.03, None, None), None),
        (
            "gemini-3.1-pro-preview",
            "gemini-3.1-pro-preview-customtools",
            2.00,
            12.00,
            (0.20, None, None),
            (200_000, 2.0, 1.5),
        ),
        ("gemini-3.1-flash-lite", None, 0.25, 1.50, (0.025, None, None), None),
        ("gemini-3-flash-preview", None, 0.50, 3.00, (0.05, None, None), None),
        ("gemini-2.5-flash-lite", None, 0.10, 0.40, (0.01, None, None), None),
        (
            "gemini-2.5-pro",
            None,
            1.25,
            10.00,
            (0.125, None, None),
            (200_000, 2.0, 1.5),
        ),
        ("gemini-2.5-flash", None, 0.30, 2.50, (0.03, None, None), None),
    ],
}

_INSERT = sa.text(
    "INSERT INTO model_pricing (provider_id, model_name, model_alias, "
    "input_price_per_million, output_price_per_million, "
    "cache_read_price_per_million, cache_write_price_per_million, "
    "cache_write_1h_price_per_million, long_context_threshold, "
    "long_context_input_multiplier, long_context_output_multiplier, "
    "is_active, created_at, updated_at) VALUES (:provider, :model, :alias, "
    ":input, :output, :read, :write, :write_1h, :threshold, :long_input, "
    ":long_output, :active, :now, :now)"
)


def upgrade() -> None:
    with op.batch_alter_table("model_pricing") as batch:
        batch.add_column(sa.Column("long_context_threshold", sa.Integer()))
        batch.add_column(sa.Column("long_context_input_multiplier", sa.Float()))
        batch.add_column(sa.Column("long_context_output_multiplier", sa.Float()))

    bind = op.get_bind()
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # storage is naive UTC
    for provider_type, models in CURRENT_MODELS.items():
        provider = bind.execute(
            sa.text(
                "SELECT id FROM providers WHERE lower(provider_type) = :type "
                "AND is_active = :active ORDER BY id LIMIT 1"
            ),
            {"type": provider_type, "active": True},
        ).scalar()
        if provider is None:
            continue
        rows = (
            bind.execute(
                sa.text(
                    "SELECT m.id, m.model_name, m.model_alias, "
                    "m.input_price_per_million AS input, "
                    "m.output_price_per_million AS output, "
                    "m.long_context_threshold AS threshold "
                    "FROM model_pricing m JOIN providers p ON p.id = m.provider_id "
                    "WHERE lower(p.provider_type) = :type"
                ),
                {"type": provider_type},
            )
            .mappings()
            .all()
        )
        for model, alias, inp, out, cache, long_context in models:
            threshold, long_input, long_output = long_context or (None, None, None)
            # A row under this name, or an operator's alias for it, already
            # prices this model: exact name would beat the alias, so never add
            # a second row.
            by_name = [r for r in rows if r["model_name"].lower() == model.lower()]
            by_alias = [
                r
                for r in rows
                if model.lower()
                in {a.strip().lower() for a in (r["model_alias"] or "").split(",")}
            ]
            if by_name or by_alias:
                # Upgrade after a downgrade: the row survived but the columns
                # did not. Restore the tier only on an untouched row.
                for row in by_name:
                    if (
                        threshold is not None
                        and row["threshold"] is None
                        and row["input"] == inp
                        and row["output"] == out
                    ):
                        bind.execute(
                            sa.text(
                                "UPDATE model_pricing SET long_context_threshold = :t, "
                                "long_context_input_multiplier = :i, "
                                "long_context_output_multiplier = :o WHERE id = :id"
                            ),
                            {
                                "t": threshold,
                                "i": long_input,
                                "o": long_output,
                                "id": row["id"],
                            },
                        )
                continue
            bind.execute(
                _INSERT,
                {
                    "provider": provider,
                    "model": model,
                    "alias": alias,
                    "input": inp,
                    "output": out,
                    "read": cache[0],
                    "write": cache[1],
                    "write_1h": cache[2],
                    "threshold": threshold,
                    "long_input": long_input,
                    "long_output": long_output,
                    "active": True,
                    "now": now,
                },
            )

    # ACC-20: only a row still at the wrong seeded price, nothing edited since.
    bind.execute(
        sa.text(
            "UPDATE model_pricing SET input_price_per_million = 2.0, "
            "output_price_per_million = 10.0, "
            "cache_read_price_per_million = 0.20, "
            "cache_write_price_per_million = 2.50, "
            "cache_write_1h_price_per_million = 4.00 "
            "WHERE model_name = 'claude-sonnet-5' "
            "AND input_price_per_million = 3.0 "
            "AND output_price_per_million = 15.0 "
            "AND cache_read_price_per_million IS NULL "
            "AND cache_write_price_per_million IS NULL "
            "AND cache_write_1h_price_per_million IS NULL"
        )
    )
    # Dated ids of models already seeded, so they price exactly, not 'estimated'.
    bind.execute(
        sa.text(
            "UPDATE model_pricing SET model_alias = 'claude-haiku-4-5-20251001' "
            "WHERE model_name = 'claude-haiku-4-5' AND model_alias IS NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("model_pricing") as batch:
        batch.drop_column("long_context_output_multiplier")
        batch.drop_column("long_context_input_multiplier")
        batch.drop_column("long_context_threshold")
