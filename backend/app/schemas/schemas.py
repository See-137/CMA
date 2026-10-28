import json
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from app.timeutils import to_naive_utc


def _utc_iso(value: datetime) -> str:
    """Serialise stored naive-UTC datetimes with an explicit "Z".

    Without a designator, browsers parse ISO strings as *local* time, shifting
    every displayed timestamp by the viewer's UTC offset.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


# Output datetimes are always UTC with "Z"; inputs accept any ISO-8601.
UTCDateTime = Annotated[datetime, PlainSerializer(_utc_iso, when_used="json")]


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
    timestamp: UTCDateTime | None = None

    @field_validator("timestamp")
    @classmethod
    def _timestamp_to_utc(cls, v: datetime | None) -> datetime | None:
        return to_naive_utc(v)


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
    cost_source: str | None = None
    over_budget: bool = False
    duration_ms: int | None = None
    status: str
    workflow: str | None = None
    swarm: str | None = None
    metadata_json: str | None = None
    timestamp: UTCDateTime
    created_at: UTCDateTime | None = None

    model_config = {"from_attributes": True}


class EventsIngestionResponse(BaseModel):
    received: int
    processed: int
    # Always 0 since budgets stopped discarding incurred spend (kept for
    # client compatibility; enforcement is GET /budgets/check before a call).
    rejected: int = 0
    # Events that failed to persist (server-side error). Non-zero means the
    # caller's data was NOT stored even though the request returned 200.
    errored: int = 0
    # Stored events that arrived while a matching blocking budget was
    # already exhausted (flagged over_budget).
    over_budget: int = 0
    budget_violations: list[str] = []
    # Deprecated, always empty (see `rejected`).
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
    created_at: UTCDateTime | None = None
    updated_at: UTCDateTime | None = None
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
    model_name: str = Field(min_length=1, max_length=255)
    input_price_per_million: float = Field(ge=0)
    output_price_per_million: float = Field(ge=0)
    model_alias: str | None = None


class ModelPricingOut(BaseModel):
    id: int
    provider_id: int
    model_name: str
    model_alias: str | None = None
    input_price_per_million: float
    output_price_per_million: float
    is_active: bool
    created_at: UTCDateTime | None = None
    updated_at: UTCDateTime | None = None

    model_config = {"from_attributes": True}


class ModelPricingUpdate(BaseModel):
    model_name: str | None = None
    model_alias: str | None = None
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
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
    created_at: UTCDateTime | None = None
    updated_at: UTCDateTime | None = None
    models: list[ModelPricingOut] = []

    model_config = {"from_attributes": True}


# -- Budgets ------------------------------------------------------------------


# Every value the UI offers must be accepted AND enforced (provider/model
# scopes and hourly periods were offered by the UI long before they were
# enforced; see FINDINGS BUD-03).
BudgetScope = Literal["global", "agent", "swarm", "workflow", "provider", "model"]
BudgetPeriod = Literal["hourly", "daily", "weekly", "monthly"]
# "block"/"throttle" are what the UI has always sent; "stop"/"rate_limit"/
# "model_downgrade" are what enforcement historically recognised. All are
# accepted so existing clients keep working; see docs/FINDINGS.md (budget
# enforcement semantics) for how they are interpreted.
BudgetAction = Literal[
    "alert", "block", "throttle", "stop", "rate_limit", "model_downgrade"
]
Threshold = Annotated[int, Field(gt=0, le=1000)]


def _strip(v: str | None) -> str | None:
    return v.strip() if isinstance(v, str) else v


def _check_scope_ref(scope: str | None, scope_ref: str | None) -> None:
    if scope and scope != "global" and not (scope_ref or "").strip():
        raise ValueError(f"scope '{scope}' requires a non-empty scope_ref")


class BudgetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scope: BudgetScope
    scope_ref: str | None = Field(default=None, max_length=255)
    period: BudgetPeriod
    limit_amount: float = Field(ge=0)
    alert_thresholds: list[Threshold] = [50, 75, 90, 100]
    control_action: BudgetAction = "alert"

    @field_validator("scope_ref", "name", mode="before")
    @classmethod
    def _trim(cls, v: str | None) -> str | None:
        # "OpenAI " would never match the provider "OpenAI".
        return _strip(v)

    @model_validator(mode="after")
    def _scope_ref_required(self):
        _check_scope_ref(self.scope, self.scope_ref)
        return self


class BudgetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    scope: BudgetScope | None = None
    scope_ref: str | None = Field(default=None, max_length=255)
    period: BudgetPeriod | None = None
    limit_amount: float | None = Field(default=None, ge=0)
    alert_thresholds: list[Threshold] | None = None
    control_action: BudgetAction | None = None
    is_active: bool | None = None

    @field_validator("scope_ref", "name", mode="before")
    @classmethod
    def _trim(cls, v: str | None) -> str | None:
        return _strip(v)


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
    created_at: UTCDateTime | None = None
    updated_at: UTCDateTime | None = None

    model_config = {"from_attributes": True}

    @field_validator("alert_thresholds", mode="before")
    @classmethod
    def parse_thresholds(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


class BudgetViolation(BaseModel):
    budget_id: int
    name: str
    scope: str
    scope_ref: str | None = None
    period: str
    limit_amount: float
    spent: float
    message: str


class BudgetCheckResponse(BaseModel):
    allowed: bool
    violations: list[BudgetViolation] = []


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
    created_at: UTCDateTime | None = None
    resolved_at: UTCDateTime | None = None

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
    created_at: UTCDateTime | None = None
    updated_at: UTCDateTime | None = None

    model_config = {"from_attributes": True}


# -- Dashboard ----------------------------------------------------------------


class DashboardOverview(BaseModel):
    total_cost: float
    total_requests: int
    active_agents: int
    total_tokens: int
    cost_change_pct: float
    request_change_pct: float
    # Events in the period stored at $0 because no price matched — the
    # total_cost understates spend while this is non-zero.
    unpriced_events: int = 0


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
    from_date: UTCDateTime | None = None
    to_date: UTCDateTime | None = None

    @field_validator("from_date", "to_date")
    @classmethod
    def _dates_to_utc(cls, v: datetime | None) -> datetime | None:
        return to_naive_utc(v)


class SemanticSearchResult(BaseModel):
    event: EventOut
    similarity_score: float
    highlight: str = ""


class SemanticSearchResponse(BaseModel):
    results: list[SemanticSearchResult]
    query_embedding_time_ms: float = 0
    search_time_ms: float = 0
