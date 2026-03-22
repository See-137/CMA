import React, { useState, useCallback } from 'react';
import {
  Plug,
  Copy,
  Check,
  Eye,
  EyeOff,
  ChevronDown,
  Zap,
  Code,
  Terminal,
  Package,
  Send,
  BarChart3,
  Search,
  Link,
} from 'lucide-react';
import Badge from '../components/Badge';

type Integration = {
  id: string;
  title: string;
  description: string;
  icon: React.FC<{ className?: string }>;
  badge: { text: string; variant: 'success' | 'info' | 'warning' | 'default' };
  language: string;
  code: string;
};

type Category = {
  id: string;
  title: string;
  description: string;
  icon: React.FC<{ className?: string }>;
  items: Integration[];
};

const Integrations: React.FC = () => {
  const [showApiKey, setShowApiKey] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [expandedCard, setExpandedCard] = useState<string | null>('auto');
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(
    new Set(['getting-started', 'llm-providers', 'observability', 'api'])
  );

  const endpoint = window.location.origin;
  const apiKey = localStorage.getItem('api_key') || '';

  const copyToClipboard = useCallback((text: string, id: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), 2000);
    });
  }, []);

  const toggleCategory = (catId: string) => {
    setExpandedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(catId)) next.delete(catId);
      else next.add(catId);
      return next;
    });
  };

  const toggleCard = (cardId: string) => {
    setExpandedCard((prev) => (prev === cardId ? null : cardId));
  };

  const maskedKey = apiKey
    ? apiKey.slice(0, 8) + '\u2022'.repeat(Math.max(0, apiKey.length - 12)) + apiKey.slice(-4)
    : 'No API key found';

  const inject = (code: string): string =>
    code.replace(/YOUR_ENDPOINT/g, endpoint).replace(/YOUR_API_KEY/g, apiKey);

  const categories: Category[] = [
    {
      id: 'getting-started',
      title: 'Getting Started',
      description: 'Install the SDK and start tracking in under a minute',
      icon: Zap,
      items: [
        {
          id: 'install',
          title: 'Install Python SDK',
          description: 'Install the CMA cost monitoring SDK from the local package.',
          icon: Package,
          badge: { text: 'Required', variant: 'warning' },
          language: 'bash',
          code: `pip install -e ./sdks/python`,
        },
        {
          id: 'auto',
          title: 'Auto-Instrumentation',
          description:
            'Zero-config setup. Automatically patches OpenAI and Anthropic clients — every LLM call is tracked with no code changes.',
          icon: Zap,
          badge: { text: 'Recommended', variant: 'success' },
          language: 'python',
          code: `from cost_monitor.auto import auto_instrument

# Add this once at the top of your app — all LLM calls are tracked automatically
auto_instrument(
    endpoint="YOUR_ENDPOINT",
    api_key="YOUR_API_KEY",
    default_agent="my-app",  # optional: name your agent
)

# Your existing code works unchanged:
# client = OpenAI()
# client.chat.completions.create(model="gpt-4o", messages=[...])  # <- auto-tracked`,
        },
      ],
    },
    {
      id: 'llm-providers',
      title: 'LLM Provider SDKs',
      description: 'Manual integration for fine-grained control over cost tracking',
      icon: Code,
      items: [
        {
          id: 'openai',
          title: 'OpenAI',
          description: 'Track GPT-4o, GPT-4, GPT-3.5 completions with full control over metadata and workflows.',
          icon: Code,
          badge: { text: 'Python', variant: 'info' },
          language: 'python',
          code: `from cost_monitor import CostTracker

tracker = CostTracker(endpoint="YOUR_ENDPOINT", api_key="YOUR_API_KEY")

# Option 1: Context manager with auto-timing
with tracker.trace_request(agent="research-bot", workflow="rag-pipeline") as ctx:
    response = client.chat.completions.create(model="gpt-4o", messages=[...])
    ctx.log_completion(
        model="gpt-4o",
        provider="openai",
        tokens_input=response.usage.prompt_tokens,
        tokens_output=response.usage.completion_tokens,
    )

# Option 2: Direct event logging
tracker.log_event(
    agent_name="research-bot",
    model="gpt-4o",
    provider="openai",
    tokens_input=1500,
    tokens_output=500,
    status="success",
)`,
        },
        {
          id: 'anthropic',
          title: 'Anthropic',
          description: 'Track Claude 3.5 Sonnet, Opus, and Haiku usage with input/output token counts.',
          icon: Code,
          badge: { text: 'Python', variant: 'info' },
          language: 'python',
          code: `from cost_monitor import CostTracker

tracker = CostTracker(endpoint="YOUR_ENDPOINT", api_key="YOUR_API_KEY")

with tracker.trace_request(agent="code-agent", workflow="code-review") as ctx:
    response = anthropic.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1024,
        messages=[{"role": "user", "content": "..."}],
    )
    ctx.log_completion(
        model="claude-3-5-sonnet",
        provider="anthropic",
        tokens_input=response.usage.input_tokens,
        tokens_output=response.usage.output_tokens,
    )`,
        },
        {
          id: 'langchain',
          title: 'LangChain',
          description: 'Drop-in callback handler for LangChain chains, agents, and tools.',
          icon: Link,
          badge: { text: 'Python', variant: 'info' },
          language: 'python',
          code: `from cost_monitor import CostTracker
from cost_monitor._langchain import CMACallbackHandler

tracker = CostTracker(endpoint="YOUR_ENDPOINT", api_key="YOUR_API_KEY")
handler = CMACallbackHandler(tracker, agent_name="langchain-agent")

# Attach to any LangChain LLM or chain
llm = ChatOpenAI(model="gpt-4o", callbacks=[handler])
chain = prompt | llm | parser  # callbacks propagate through the chain`,
        },
        {
          id: 'llamaindex',
          title: 'LlamaIndex',
          description: 'Track LlamaIndex query engine and retrieval pipeline costs.',
          icon: Link,
          badge: { text: 'Python', variant: 'info' },
          language: 'python',
          code: `from cost_monitor import CostTracker

tracker = CostTracker(endpoint="YOUR_ENDPOINT", api_key="YOUR_API_KEY")

# Use the context manager around LlamaIndex queries
with tracker.trace_request(agent="llama-agent", workflow="query-engine") as ctx:
    response = query_engine.query("What is the revenue for Q4?")
    # LlamaIndex exposes token counts in the response metadata
    if hasattr(response, "metadata") and "token_count" in response.metadata:
        ctx.log_completion(
            model=response.metadata.get("model_name", "gpt-4o"),
            provider="openai",
            tokens_input=response.metadata.get("prompt_tokens", 0),
            tokens_output=response.metadata.get("completion_tokens", 0),
        )`,
        },
      ],
    },
    {
      id: 'observability',
      title: 'Recipes & Examples',
      description: 'Example scripts for exporting CMA data to external platforms',
      icon: BarChart3,
      items: [
        {
          id: 'langfuse',
          title: 'Langfuse',
          description: 'Send LLM traces and cost data to Langfuse for deeper analytics and prompt debugging.',
          icon: Search,
          badge: { text: 'Export', variant: 'default' },
          language: 'python',
          code: `# Use Langfuse alongside CMA — both track via callbacks
from langfuse.callback import CallbackHandler as LangfuseHandler
from cost_monitor._langchain import CMACallbackHandler
from cost_monitor import CostTracker

tracker = CostTracker(endpoint="YOUR_ENDPOINT", api_key="YOUR_API_KEY")

langfuse_handler = LangfuseHandler(
    public_key="pk-lf-...",
    secret_key="sk-lf-...",
    host="https://cloud.langfuse.com",
)
cma_handler = CMACallbackHandler(tracker, agent_name="my-agent")

# Both handlers run side by side
llm = ChatOpenAI(model="gpt-4o", callbacks=[langfuse_handler, cma_handler])`,
        },
        {
          id: 'datadog',
          title: 'Datadog',
          description: 'Export CMA cost metrics to Datadog via DogStatsD for dashboards and alerting.',
          icon: BarChart3,
          badge: { text: 'Export', variant: 'default' },
          language: 'python',
          code: `# Push CMA metrics to Datadog using DogStatsD
from datadog import statsd
import httpx

# Poll CMA dashboard API and push to Datadog
def push_to_datadog(cma_endpoint: str, api_key: str):
    headers = {"Authorization": f"Bearer {api_key}"}
    r = httpx.get(f"{cma_endpoint}/api/v1/dashboard/overview?period=today", headers=headers)
    data = r.json()

    statsd.gauge("cma.total_cost", data["total_cost"], tags=["env:prod"])
    statsd.gauge("cma.total_requests", data["total_requests"], tags=["env:prod"])
    statsd.gauge("cma.active_agents", data["active_agents"], tags=["env:prod"])

    # Per-agent breakdown
    r = httpx.get(f"{cma_endpoint}/api/v1/dashboard/top-agents?period=today", headers=headers)
    for agent in r.json()["data"]:
        statsd.gauge("cma.agent_cost", agent["total_cost"],
                     tags=[f"agent:{agent['agent_name']}", "env:prod"])

# Run on a schedule (e.g., every 60s via cron or APScheduler)
push_to_datadog("YOUR_ENDPOINT", "YOUR_API_KEY")`,
        },
        {
          id: 'elastic',
          title: 'Elasticsearch',
          description: 'Stream CMA events to Elasticsearch for full-text search, Kibana dashboards, and long-term analytics.',
          icon: Search,
          badge: { text: 'Export', variant: 'default' },
          language: 'python',
          code: `# Stream CMA events to Elasticsearch
from elasticsearch import Elasticsearch
import httpx

es = Elasticsearch("http://localhost:9200")

def sync_to_elastic(cma_endpoint: str, api_key: str, since_id: int = 0):
    """Fetch recent events from CMA and index into Elasticsearch."""
    headers = {"Authorization": f"Bearer {api_key}"}
    r = httpx.get(
        f"{cma_endpoint}/api/v1/events",
        params={"per_page": 100, "page": 1},
        headers=headers,
    )
    events = r.json()["items"]

    for event in events:
        es.index(
            index="cma-events",
            id=event["id"],
            document={
                "agent": event["agent_name"],
                "model": event["model_name"],
                "provider": event["provider_name"],
                "cost": event["cost"],
                "tokens_in": event["tokens_input"],
                "tokens_out": event["tokens_output"],
                "status": event["status"],
                "timestamp": event["timestamp"],
            },
        )

# Run on a schedule (e.g., every 30s)
sync_to_elastic("YOUR_ENDPOINT", "YOUR_API_KEY")`,
        },
        {
          id: 'prometheus',
          title: 'Prometheus',
          description: 'Expose CMA metrics as a Prometheus scrape endpoint for Grafana dashboards.',
          icon: BarChart3,
          badge: { text: 'Export', variant: 'default' },
          language: 'python',
          code: `# Expose CMA metrics for Prometheus scraping
from prometheus_client import Gauge, start_http_server
import httpx, time

cost_gauge = Gauge("cma_total_cost_usd", "Total LLM cost", ["period"])
requests_gauge = Gauge("cma_total_requests", "Total LLM requests", ["period"])
agent_cost = Gauge("cma_agent_cost_usd", "Cost per agent", ["agent"])

def collect(cma_endpoint: str, api_key: str):
    headers = {"Authorization": f"Bearer {api_key}"}
    overview = httpx.get(f"{cma_endpoint}/api/v1/dashboard/overview?period=today", headers=headers).json()
    cost_gauge.labels(period="today").set(overview["total_cost"])
    requests_gauge.labels(period="today").set(overview["total_requests"])

    agents = httpx.get(f"{cma_endpoint}/api/v1/dashboard/top-agents?period=today", headers=headers).json()
    for a in agents["data"]:
        agent_cost.labels(agent=a["agent_name"]).set(a["total_cost"])

# Start Prometheus metrics server on :9091
start_http_server(9091)
while True:
    collect("YOUR_ENDPOINT", "YOUR_API_KEY")
    time.sleep(30)`,
        },
      ],
    },
    {
      id: 'api',
      title: 'Direct API',
      description: 'Use the REST API from any language or tool',
      icon: Terminal,
      items: [
        {
          id: 'rest-post',
          title: 'Send Events (POST)',
          description: 'Ingest cost events via HTTP from any language — Python, Node.js, Go, or curl.',
          icon: Send,
          badge: { text: 'Any Language', variant: 'success' },
          language: 'bash',
          code: `# Single event
curl -X POST YOUR_ENDPOINT/api/v1/events \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "agent_name": "my-agent",
    "model": "gpt-4o",
    "provider": "openai",
    "tokens_input": 1500,
    "tokens_output": 500,
    "status": "success",
    "workflow": "rag-pipeline"
  }'

# Batch (array of events)
curl -X POST YOUR_ENDPOINT/api/v1/events \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '[
    {"agent_name":"bot-1","model":"gpt-4o","provider":"openai","tokens_input":1000,"tokens_output":300},
    {"agent_name":"bot-2","model":"claude-3.5-sonnet","provider":"anthropic","tokens_input":2000,"tokens_output":800}
  ]'`,
        },
        {
          id: 'rest-query',
          title: 'Query Data (GET)',
          description: 'Read cost data, agent stats, and dashboard metrics via the REST API.',
          icon: Terminal,
          badge: { text: 'Any Language', variant: 'success' },
          language: 'bash',
          code: `# Dashboard overview
curl YOUR_ENDPOINT/api/v1/dashboard/overview?period=today \\
  -H "Authorization: Bearer YOUR_API_KEY"

# List agents with cost stats
curl YOUR_ENDPOINT/api/v1/agents \\
  -H "Authorization: Bearer YOUR_API_KEY"

# Query events with filters
curl "YOUR_ENDPOINT/api/v1/events?agent_name=my-agent&status=success&per_page=10" \\
  -H "Authorization: Bearer YOUR_API_KEY"

# Budget status
curl YOUR_ENDPOINT/api/v1/dashboard/budget-status \\
  -H "Authorization: Bearer YOUR_API_KEY"`,
        },
      ],
    },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Integrations</h1>
        <p className="text-sm text-slate-500 mt-1">
          Connect your AI agents and export data to external platforms
        </p>
      </div>

      {/* Connection Info */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
        <div className="flex items-center gap-2 mb-4">
          <Plug className="h-5 w-5 text-teal-600" />
          <h2 className="text-base font-semibold text-slate-900">Connection Info</h2>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wider">
              API Endpoint
            </label>
            <div className="flex items-center gap-2">
              <div className="flex-1 bg-slate-900 text-green-400 font-mono text-sm px-3 py-2.5 rounded-lg truncate">
                {endpoint}
              </div>
              <CopyBtn text={endpoint} id="endpoint" copiedId={copiedId} onCopy={copyToClipboard} />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wider">
              API Key
            </label>
            <div className="flex items-center gap-2">
              <div className="flex-1 bg-slate-900 text-green-400 font-mono text-sm px-3 py-2.5 rounded-lg truncate">
                {showApiKey ? apiKey || 'No API key found' : maskedKey}
              </div>
              <button
                onClick={() => setShowApiKey((v) => !v)}
                className="inline-flex items-center justify-center h-9 w-9 rounded-lg bg-slate-100 text-slate-500 hover:bg-slate-200 hover:text-slate-700 transition-colors"
                title={showApiKey ? 'Hide' : 'Show'}
              >
                {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
              <CopyBtn text={apiKey} id="apikey" copiedId={copiedId} onCopy={copyToClipboard} />
            </div>
          </div>
        </div>
      </div>

      {/* Categories */}
      <div className="space-y-4">
        {categories.map((cat) => {
          const isOpen = expandedCategories.has(cat.id);
          return (
            <div key={cat.id} className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
              {/* Category Header */}
              <button
                onClick={() => toggleCategory(cat.id)}
                className="w-full flex items-center justify-between px-6 py-4 text-left hover:bg-slate-50 transition-colors"
              >
                <div className="flex items-center gap-3">
                  <div className="flex items-center justify-center h-10 w-10 rounded-xl bg-teal-50 text-teal-600">
                    <cat.icon className="h-5 w-5" />
                  </div>
                  <div>
                    <h2 className="text-sm font-semibold text-slate-900">{cat.title}</h2>
                    <p className="text-xs text-slate-500">{cat.description}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-slate-400 font-medium">
                    {cat.items.length} integration{cat.items.length !== 1 ? 's' : ''}
                  </span>
                  <ChevronDown
                    className={`h-4 w-4 text-slate-400 transition-transform duration-200 ${
                      isOpen ? 'rotate-180' : ''
                    }`}
                  />
                </div>
              </button>

              {/* Category Items */}
              {isOpen && (
                <div className="border-t border-slate-100">
                  {cat.items.map((item, idx) => {
                    const isExpanded = expandedCard === item.id;
                    const resolvedCode = inject(item.code);
                    return (
                      <div
                        key={item.id}
                        className={idx < cat.items.length - 1 ? 'border-b border-slate-100' : ''}
                      >
                        {/* Item Header */}
                        <button
                          onClick={() => toggleCard(item.id)}
                          className="w-full flex items-center justify-between px-6 py-3.5 text-left hover:bg-slate-50/50 transition-colors"
                        >
                          <div className="flex items-center gap-3">
                            <item.icon className="h-4 w-4 text-slate-400" />
                            <div>
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-slate-800">{item.title}</span>
                                <Badge text={item.badge.text} variant={item.badge.variant} />
                              </div>
                              <p className="text-xs text-slate-500 mt-0.5">{item.description}</p>
                            </div>
                          </div>
                          <ChevronDown
                            className={`h-4 w-4 text-slate-300 transition-transform duration-200 shrink-0 ml-4 ${
                              isExpanded ? 'rotate-180' : ''
                            }`}
                          />
                        </button>

                        {/* Code Block */}
                        {isExpanded && (
                          <div className="mx-6 mb-4">
                            <div className="relative bg-slate-900 rounded-lg overflow-hidden">
                              <div className="flex items-center justify-between px-4 py-2 bg-slate-800/50 border-b border-slate-700/50">
                                <span className="text-xs font-mono text-slate-400">{item.language}</span>
                                <CopyBtn
                                  text={resolvedCode}
                                  id={item.id}
                                  copiedId={copiedId}
                                  onCopy={copyToClipboard}
                                />
                              </div>
                              <pre className="p-4 overflow-x-auto">
                                <code className="text-green-400 font-mono text-xs leading-relaxed whitespace-pre">
                                  {resolvedCode}
                                </code>
                              </pre>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

const CopyBtn: React.FC<{
  text: string;
  id: string;
  copiedId: string | null;
  onCopy: (text: string, id: string) => void;
}> = ({ text, id, copiedId, onCopy }) => (
  <button
    onClick={(e) => {
      e.stopPropagation();
      onCopy(text, id);
    }}
    className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-md transition-colors shrink-0 ${
      copiedId === id
        ? 'bg-emerald-600 text-white'
        : 'bg-slate-700 text-slate-300 hover:bg-slate-600 hover:text-white'
    }`}
  >
    {copiedId === id ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
    {copiedId === id ? 'Copied!' : 'Copy'}
  </button>
);

export default Integrations;
