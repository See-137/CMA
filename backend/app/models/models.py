from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.timeutils import utcnow


def _utcnow():
    return utcnow()


class SetupConfig(Base):
    __tablename__ = "setup_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    deployment_name = Column(String(255), default="CMA")
    timezone = Column(String(50), default="UTC")
    currency = Column(String(10), default="USD")
    admin_email = Column(String(255), nullable=False)
    admin_password_hash = Column(String(255), nullable=False)
    data_retention_days = Column(Integer, default=90)
    discovery_mode = Column(String(50), default="auto_discover")
    api_key = Column(String(255), nullable=False)
    is_setup_complete = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Provider(Base):
    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    provider_type = Column(String(50), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    models = relationship("ModelPricing", back_populates="provider", lazy="joined")


class ModelPricing(Base):
    __tablename__ = "model_pricing"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False)
    model_name = Column(String(255), nullable=False)
    model_alias = Column(String(255), nullable=True)
    input_price_per_million = Column(Float, nullable=False)
    output_price_per_million = Column(Float, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    provider = relationship("Provider", back_populates="models")

    __table_args__ = (
        UniqueConstraint("provider_id", "model_name", name="uq_provider_model"),
    )


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(String(500), nullable=True)
    environment = Column(String(50), default="dev")
    swarm = Column(String(255), nullable=True)
    workflow = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    events = relationship("CostEvent", back_populates="agent")


class CostEvent(Base):
    __tablename__ = "cost_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String(255), index=True, nullable=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)
    agent_name = Column(String(255), nullable=False)
    model_name = Column(String(255), nullable=False)
    provider_name = Column(String(255), nullable=False)
    tokens_input = Column(Integer, nullable=False)
    tokens_output = Column(Integer, nullable=False)
    cost = Column(Float, nullable=False)
    duration_ms = Column(Integer, nullable=True)
    status = Column(String(50), default="success")
    workflow = Column(String(255), nullable=True)
    swarm = Column(String(255), nullable=True)
    metadata_json = Column(Text, nullable=True)
    timestamp = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    agent = relationship("Agent", back_populates="events")

    __table_args__ = (
        Index("ix_cost_events_timestamp", "timestamp"),
        Index("ix_cost_events_agent_name", "agent_name"),
    )


class Budget(Base):
    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    scope = Column(String(50), nullable=False)
    scope_ref = Column(String(255), nullable=True)
    period = Column(String(50), nullable=False)
    limit_amount = Column(Float, nullable=False)
    alert_thresholds = Column(Text, default="[50, 75, 90, 100]")
    control_action = Column(String(50), default="alert")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    alerts = relationship("Alert", back_populates="budget")


class AlertChannel(Base):
    __tablename__ = "alert_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    channel_type = Column(String(50), nullable=False)
    config_json = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    alerts = relationship("Alert", back_populates="alert_channel")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    budget_id = Column(Integer, ForeignKey("budgets.id"), nullable=True)
    alert_channel_id = Column(Integer, ForeignKey("alert_channels.id"), nullable=True)
    alert_type = Column(String(50), nullable=False)
    message = Column(Text, nullable=False)
    severity = Column(String(50), default="info")
    is_resolved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)
    resolved_at = Column(DateTime, nullable=True)

    budget = relationship("Budget", back_populates="alerts")
    alert_channel = relationship("AlertChannel", back_populates="alerts")


class DailyCostRollup(Base):
    __tablename__ = "daily_cost_rollups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    agent_name = Column(String(255), nullable=False)
    model_name = Column(String(255), nullable=False)
    provider_name = Column(String(255), nullable=False)
    total_cost = Column(Float, default=0)
    total_tokens_input = Column(Integer, default=0)
    total_tokens_output = Column(Integer, default=0)
    request_count = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    avg_duration_ms = Column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "date",
            "agent_name",
            "model_name",
            "provider_name",
            name="uq_daily_rollup",
        ),
    )
