import json
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# -- Setup --------------------------------------------------------------------


class SetupRequest(BaseModel):
    admin_email: str
    admin_password: str
    deployment_name: str = "CMA"
    timezone: str = "UTC"
    currency: str = "USD"


class SetupResponse(BaseModel):
    message: str
    api_key: str
    is_complete: bool


class SetupStatusResponse(BaseModel):
    is_complete: bool
    deployment_name: str | None = None
    timezone: str | None = None
    currency: str | None = None


# -- Auth ---------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    api_key: str
    email: str


# -- Events -------------------------------------------------------------------


class EventIn(BaseModel):
    trace_id: str | None = Field(default=None, max_length=255)
    agent_name: str = Field(min_length=1, max_length=255)
    model: str = Field(min_length=1, max_length=255)
    provider: str = Field(min_length=1, max_length=255)
    tokens_input: int = Field(ge=0)
    tokens_output: int = Field(ge=0)
    cost: float | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    status: str = Field(default="success", max_length=50)
    workflow: str | None = Field(default=None, max_length=255)
    swarm: str | None = Field(default=None, max_length=255)
    metadata: dict | None = None
    timestamp: datetime | None = None


class EventsEnvelope(BaseModel):
    """Wrapper shape the SDK sends: {"events": [...]}."""

    events: list[EventIn]


class EventOut(BaseModel):
    id: int
    trace_id: str | None = None
    agent_name: str
    model_name: str
    provider_name: str
    tokens_input: int
    tokens_output: int
    cost: float
    duration_ms: int | None = None
    status: str
    workflow: str | None = None
    swarm: str | None = None
    metadata_json: str | None = None
    timestamp: datetime
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class EventsIngestionResponse(BaseModel):
    received: int
    processed: int
    rejected: int = 0
    rejection_reasons: list[str] = []


class PaginatedEvents(BaseModel):
    items: list[EventOut]
    total: int
    page: int
    per_page: int


# -- Agents -------------------------------------------------------------------


class AgentCreate(BaseModel):
    name: str
    description: str | None = None
    environment: str = "dev"
    swarm: str | None = None
    workflow: str | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    environment: str | None = None
    swarm: str | None = None
    workflow: str | None = None
    is_active: bool | None = None


class AgentOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    environment: str
    swarm: str | None = None
    workflow: str | None = None
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
    total_cost: float = 0.0
    request_count: int = 0

    model_config = {"from_attributes": True}


class ModelUsedItem(BaseModel):
    model_name: str
    provider_name: str
    total_cost: float
    request_count: int


class AgentDetail(AgentOut):
    total_tokens: int = 0
    models_used: list[ModelUsedItem] = []
    recent_events: list[EventOut] = []


# -- Providers ----------------------------------------------------------------


class ModelPricingIn(BaseModel):
    model_name: str
    input_price_per_million: float
    output_price_per_million: float
    model_alias: str | None = None


class ModelPricingOut(BaseModel):
    id: int
    provider_id: int
    model_name: str
    model_alias: str | None = None
    input_price_per_million: float
    output_price_per_million: float
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ModelPricingUpdate(BaseModel):
    model_name: str | None = None
    model_alias: str | None = None
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    is_active: bool | None = None


class ProviderCreate(BaseModel):
    name: str
    provider_type: str
    models: list[ModelPricingIn] = []


class ProviderUpdate(BaseModel):
    name: str | None = None
    provider_type: str | None = None
    is_active: bool | None = None


class ProviderOut(BaseModel):
    id: int
    name: str
    provider_type: str
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
    models: list[ModelPricingOut] = []

    model_config = {"from_attributes": True}


# -- Budgets ------------------------------------------------------------------


class BudgetCreate(BaseModel):
    name: str
    scope: str
    scope_ref: str | None = None
    period: str
    limit_amount: float
    alert_thresholds: list[int] = [50, 75, 90, 100]
    control_action: str = "alert"


class BudgetUpdate(BaseModel):
    name: str | None = None
    scope: str | None = None
    scope_ref: str | None = None
    period: str | None = None
    limit_amount: float | None = None
    alert_thresholds: list[int] | None = None
    control_action: str | None = None
    is_active: bool | None = None


class BudgetOut(BaseModel):
    id: int
    name: str
    scope: str
    scope_ref: str | None = None
    period: str
    limit_amount: float
    alert_thresholds: list[int]
    control_action: str
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_validator("alert_thresholds", mode="before")
    @classmethod
    def parse_thresholds(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


class BudgetWithSpend(BudgetOut):
    current_spend: float = 0.0
    percentage: float = 0.0


# -- Alerts -------------------------------------------------------------------


class AlertOut(BaseModel):
    id: int
    budget_id: int | None = None
    alert_channel_id: int | None = None
    alert_type: str
    message: str
    severity: str
    is_resolved: bool
    created_at: datetime | None = None
    resolved_at: datetime | None = None

    model_config = {"from_attributes": True}


class AlertChannelCreate(BaseModel):
    name: str
    channel_type: str
    config: str


class AlertChannelOut(BaseModel):
    id: int
    name: str
    channel_type: str
    config_json: str
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


# -- Dashboard ----------------------------------------------------------------


class DashboardOverview(BaseModel):
    total_cost: float
    total_requests: int
    active_agents: int
    total_tokens: int
    cost_change_pct: float
    request_change_pct: float


class TimeseriesPoint(BaseModel):
    timestamp: str
    cost: float
    requests: int


class TimeseriesResponse(BaseModel):
    data: list[TimeseriesPoint]


class TopAgentItem(BaseModel):
    agent_name: str
    total_cost: float
    request_count: int
    avg_cost: float


class TopAgentsResponse(BaseModel):
    data: list[TopAgentItem]


class ModelUsageItem(BaseModel):
    model_name: str
    provider: str
    total_cost: float
    request_count: int
    token_count: int


class ModelUsageResponse(BaseModel):
    data: list[ModelUsageItem]


class BudgetStatusItem(BaseModel):
    budget_id: int
    name: str
    scope: str
    limit_amount: float
    spent: float
    percentage: float
    status: str


class BudgetStatusResponse(BaseModel):
    data: list[BudgetStatusItem]


# -- Chat / RAG ---------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default=[], max_length=50)
    stream: bool = False


class CitationOut(BaseModel):
    source_type: str
    source_id: str | None = None
    snippet: str = ""


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitationOut] = []
    model_used: str = ""
    usage: dict = {}


# -- Semantic search -----------------------------------------------------------


class SemanticSearchRequest(BaseModel):
    query: str
    top_k: int = 20
    agent_name: str | None = None
    provider: str | None = None
    model: str | None = None
    status: str | None = None
    from_date: datetime | None = None
    to_date: datetime | None = None


class SemanticSearchResult(BaseModel):
    event: EventOut
    similarity_score: float
    highlight: str = ""


class SemanticSearchResponse(BaseModel):
    results: list[SemanticSearchResult]
    query_embedding_time_ms: float = 0
    search_time_ms: float = 0
