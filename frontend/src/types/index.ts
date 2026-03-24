export interface SetupStatus {
  is_complete: boolean;
  deployment_name: string;
  timezone: string;
  currency: string;
}

export interface SetupRequest {
  admin_email: string;
  admin_password: string;
  deployment_name?: string;
  timezone?: string;
  currency?: string;
}

export interface SetupResponse {
  message: string;
  api_key: string;
  is_complete: boolean;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  api_key: string;
  email: string;
}

export interface Event {
  id: number;
  trace_id: string;
  agent_name: string;
  model_name: string;
  provider_name: string;
  tokens_input: number;
  tokens_output: number;
  cost: number;
  duration_ms: number | null;
  status: string;
  workflow: string | null;
  swarm: string | null;
  timestamp: string;
}

export interface PaginatedEvents {
  items: Event[];
  total: number;
  page: number;
  per_page: number;
}

export interface Agent {
  id: number;
  name: string;
  description: string | null;
  environment: string;
  swarm: string | null;
  workflow: string | null;
  is_active: boolean;
  created_at: string;
  total_cost: number;
  request_count: number;
}

export interface ModelUsed {
  model_name: string;
  provider_name: string;
  total_cost: number;
  request_count: number;
}

export interface AgentDetail extends Agent {
  total_tokens: number;
  models_used: ModelUsed[];
  recent_events: Event[];
}

export interface Model {
  id: number;
  model_name: string;
  input_price_per_million: number;
  output_price_per_million: number;
  is_active: boolean;
}

export interface Provider {
  id: number;
  name: string;
  provider_type: string;
  is_active: boolean;
  models: Model[];
}

export interface Budget {
  id: number;
  name: string;
  scope: string;
  scope_ref: string | null;
  period: string;
  limit_amount: number;
  alert_thresholds: number[];
  control_action: string;
  is_active: boolean;
  current_spend: number;
  percentage: number;
}

export interface Alert {
  id: number;
  alert_type: string;
  message: string;
  severity: string;
  is_resolved: boolean;
  created_at: string;
  resolved_at: string | null;
}

export interface AlertChannel {
  id: number;
  name: string;
  channel_type: string;
  config_json: string;
  is_active: boolean;
}

export interface DashboardOverview {
  total_cost: number;
  total_requests: number;
  active_agents: number;
  total_tokens: number;
  cost_change_pct: number;
  request_change_pct: number;
}

export interface TimeseriesPoint {
  timestamp: string;
  cost: number;
  requests: number;
}

export interface TimeseriesResponse {
  data: TimeseriesPoint[];
}

export interface TopAgent {
  agent_name: string;
  total_cost: number;
  request_count: number;
  avg_cost: number;
}

export interface TopAgentsResponse {
  data: TopAgent[];
}

export interface ModelUsageItem {
  model_name: string;
  provider: string;
  total_cost: number;
  request_count: number;
  token_count: number;
}

export interface ModelUsageResponse {
  data: ModelUsageItem[];
}

export interface BudgetStatus {
  budget_id: number;
  name: string;
  scope: string;
  limit_amount: number;
  spent: number;
  percentage: number;
  status: string;
}

export interface BudgetStatusResponse {
  data: BudgetStatus[];
}

// -- Chat / RAG ---------------------------------------------------------------

export interface Citation {
  source_type: string;
  source_id?: string;
  snippet: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  timestamp?: string;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  model_used: string;
  usage: Record<string, number>;
}

export interface ChatStatus {
  vector_store: string;
  events_indexed: number;
  rollups_indexed: number;
  llm_provider: string;
  llm_model: string;
  llm_available: boolean;
  embedding_model: string;
}

// -- Semantic search ----------------------------------------------------------

export interface SemanticSearchResult {
  event: Event;
  similarity_score: number;
  highlight: string;
}

export interface SemanticSearchResponse {
  results: SemanticSearchResult[];
  query_embedding_time_ms: number;
  search_time_ms: number;
}
