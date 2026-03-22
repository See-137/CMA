"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # setup_config
    op.create_table(
        "setup_config",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("deployment_name", sa.String(255), server_default="CMA"),
        sa.Column("timezone", sa.String(50), server_default="UTC"),
        sa.Column("currency", sa.String(10), server_default="USD"),
        sa.Column("admin_email", sa.String(255), nullable=False),
        sa.Column("admin_password_hash", sa.String(255), nullable=False),
        sa.Column("data_retention_days", sa.Integer(), server_default="90"),
        sa.Column("discovery_mode", sa.String(50), server_default="auto_discover"),
        sa.Column("api_key", sa.String(255), nullable=False),
        sa.Column("is_setup_complete", sa.Boolean(), server_default="0"),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.PrimaryKeyConstraint("id"),
    )

    # providers
    op.create_table(
        "providers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider_type", sa.String(50), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="1"),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # model_pricing
    op.create_table(
        "model_pricing",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("model_alias", sa.String(255), nullable=True),
        sa.Column("input_price_per_million", sa.Float(), nullable=False),
        sa.Column("output_price_per_million", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="1"),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_name", name="uq_provider_model"),
    )

    # agents
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("environment", sa.String(50), server_default="dev"),
        sa.Column("swarm", sa.String(255), nullable=True),
        sa.Column("workflow", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="1"),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # cost_events
    op.create_table(
        "cost_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(255), nullable=True),
        sa.Column("agent_id", sa.Integer(), nullable=True),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("provider_name", sa.String(255), nullable=False),
        sa.Column("tokens_input", sa.Integer(), nullable=False),
        sa.Column("tokens_output", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(50), server_default="success"),
        sa.Column("workflow", sa.String(255), nullable=True),
        sa.Column("swarm", sa.String(255), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cost_events_trace_id", "cost_events", ["trace_id"])
    op.create_index("ix_cost_events_timestamp", "cost_events", ["timestamp"])
    op.create_index("ix_cost_events_agent_name", "cost_events", ["agent_name"])

    # budgets
    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("scope", sa.String(50), nullable=False),
        sa.Column("scope_ref", sa.String(255), nullable=True),
        sa.Column("period", sa.String(50), nullable=False),
        sa.Column("limit_amount", sa.Float(), nullable=False),
        sa.Column("alert_thresholds", sa.Text(), server_default="[50, 75, 90, 100]"),
        sa.Column("control_action", sa.String(50), server_default="alert"),
        sa.Column("is_active", sa.Boolean(), server_default="1"),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.PrimaryKeyConstraint("id"),
    )

    # alert_channels
    op.create_table(
        "alert_channels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("channel_type", sa.String(50), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="1"),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.PrimaryKeyConstraint("id"),
    )

    # alerts
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("budget_id", sa.Integer(), nullable=True),
        sa.Column("alert_channel_id", sa.Integer(), nullable=True),
        sa.Column("alert_type", sa.String(50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(50), server_default="info"),
        sa.Column("is_resolved", sa.Boolean(), server_default="0"),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["budget_id"], ["budgets.id"]),
        sa.ForeignKeyConstraint(["alert_channel_id"], ["alert_channels.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # daily_cost_rollups
    op.create_table(
        "daily_cost_rollups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("provider_name", sa.String(255), nullable=False),
        sa.Column("total_cost", sa.Float(), server_default="0"),
        sa.Column("total_tokens_input", sa.Integer(), server_default="0"),
        sa.Column("total_tokens_output", sa.Integer(), server_default="0"),
        sa.Column("request_count", sa.Integer(), server_default="0"),
        sa.Column("failure_count", sa.Integer(), server_default="0"),
        sa.Column("avg_duration_ms", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "date",
            "agent_name",
            "model_name",
            "provider_name",
            name="uq_daily_rollup",
        ),
    )


def downgrade() -> None:
    op.drop_table("daily_cost_rollups")
    op.drop_table("alerts")
    op.drop_table("alert_channels")
    op.drop_table("budgets")
    op.drop_index("ix_cost_events_agent_name", table_name="cost_events")
    op.drop_index("ix_cost_events_timestamp", table_name="cost_events")
    op.drop_index("ix_cost_events_trace_id", table_name="cost_events")
    op.drop_table("cost_events")
    op.drop_table("agents")
    op.drop_table("model_pricing")
    op.drop_table("providers")
    op.drop_table("setup_config")
