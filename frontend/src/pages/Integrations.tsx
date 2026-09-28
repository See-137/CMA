import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Plug,
  Copy,
  Check,
  Eye,
  EyeOff,
  Zap,
  Terminal,
  Package,
  Send,
  Search,
  Boxes,
  Microscope,
  ArrowRight,
  CircleDot,
} from 'lucide-react';
import {
  SiAnthropic,
  SiLangchain,
  SiDatadog,
  SiElasticsearch,
  SiPrometheus,
} from '@icons-pack/react-simple-icons';
import Badge from '../components/Badge';
import Drawer from '../components/Drawer';
import { api, ApiError } from '../api/client';
import { useLocalStorage } from '../hooks/useLocalStorage';
import { timeAgo, formatNumber } from '../utils/format';
import type { PaginatedEvents } from '../types';

type BadgeVariant = 'success' | 'info' | 'warning' | 'default';

interface Integration {
  id: string;
  title: string;
  description: string;
  logo: React.ReactNode;
  badge: { text: string; variant: BadgeVariant };
  language: string;
  code: string;
  categoryId: string;
}

const logoCls = 'h-5 w-5';

// Inlined: simple-icons drops the OpenAI mark (trademark), so we ship our own.
function OpenAiLogo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
      <path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z" />
    </svg>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function Integrations() {
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [showApiKey, setShowApiKey] = useState(false);
  const [activeCat, setActiveCat] = useState<string>('all');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Integration | null>(null);

  // Live ingest status
  const [eventCount, setEventCount] = useState<number | null>(null);
  const [lastEvent, setLastEvent] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [testState, setTestState] = useState<'idle' | 'sending' | 'ok' | 'err'>('idle');
  const [testMsg, setTestMsg] = useState<string | null>(null);

  const endpoint = window.location.origin;
  const apiKey = useLocalStorage('api_key') ?? '';
  const maskedKey = apiKey
    ? `${apiKey.slice(0, 6)}${'•'.repeat(Math.max(0, apiKey.length - 10))}${apiKey.slice(-4)}`
    : 'No API key found';

  const copyToClipboard = useCallback((text: string, id: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), 2000);
    });
  }, []);

  // Copy gets the real key; the on-screen snippet only ever shows it masked,
  // so a screenshot or screen share cannot leak it.
  const inject = (code: string): string =>
    code.replace(/YOUR_ENDPOINT/g, endpoint).replace(/YOUR_API_KEY/g, apiKey);
  const injectMasked = (code: string): string =>
    code.replace(/YOUR_ENDPOINT/g, endpoint).replace(/YOUR_API_KEY/g, maskedKey);

  const fetchStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const data = await api.get<PaginatedEvents>('/api/v1/events', { per_page: 1 });
      setEventCount(data.total);
      setLastEvent(data.items[0]?.timestamp ?? null);
    } catch {
      setEventCount(null);
      setLastEvent(null);
    } finally {
      setStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const sendTestEvent = async () => {
    setTestState('sending');
    setTestMsg(null);
    try {
      await api.post('/api/v1/events', {
        agent_name: 'integration-test',
        model: 'gpt-4o',
        provider: 'OpenAI',
        tokens_input: 100,
        tokens_output: 50,
        status: 'success',
        workflow: 'connection-check',
      });
      setTestState('ok');
      setTestMsg('Round-trip confirmed — a test event was ingested.');
      // Give the background insert a beat, then refresh the counter.
      setTimeout(fetchStatus, 600);
      setTimeout(() => setTestState('idle'), 4000);
    } catch (err) {
      setTestState('err');
      setTestMsg(
        err instanceof ApiError
          ? (err.body as { detail?: string })?.detail ?? err.message
          : 'Failed to send test event.',
      );
    }
  };

  const categories = useMemo(
    () => [
      { id: 'getting-started', title: 'Getting Started' },
      { id: 'sdks', title: 'SDKs' },
      { id: 'recipes', title: 'Recipes' },
      { id: 'api', title: 'Direct API' },
    ],
    [],
  );

  const integrations: Integration[] = useMemo(
    () => [
      {
        id: 'install',
        categoryId: 'getting-started',
        title: 'Install the SDK',
        description: 'Install the CMA cost-monitoring SDK from the local package.',
        logo: <Package className={logoCls} />,
        badge: { text: 'Required', variant: 'warning' },
        language: 'bash',
        code: `pip install -e ./sdks/python`,
      },
      {
        id: 'auto',
        categoryId: 'getting-started',
        title: 'Auto-Instrumentation',
        description: 'Zero-config. Patches OpenAI & Anthropic — every call tracked, no code changes.',
        logo: <Zap className={logoCls} />,
        badge: { text: 'Recommended', variant: 'success' },
        language: 'python',
        code: `from cost_monitor.auto import auto_instrument

# Add once at the top of your app — all LLM calls are tracked automatically
auto_instrument(
    endpoint="YOUR_ENDPOINT",
    api_key="YOUR_API_KEY",
    default_agent="my-app",  # optional: name your agent
)

# Your existing code works unchanged:
# client = OpenAI()
# client.chat.completions.create(model="gpt-4o", messages=[...])  # <- auto-tracked`,
      },
      {
        id: 'openai',
        categoryId: 'sdks',
        title: 'OpenAI',
        description: 'Track GPT-4o / GPT-4 / GPT-3.5 with full control over metadata and workflows.',
        logo: <OpenAiLogo className={logoCls} />,
        badge: { text: 'Python', variant: 'info' },
        language: 'python',
        code: `from cost_monitor import CostTracker

tracker = CostTracker(endpoint="YOUR_ENDPOINT", api_key="YOUR_API_KEY")

with tracker.trace_request(agent="research-bot", workflow="rag-pipeline") as ctx:
    response = client.chat.completions.create(model="gpt-4o", messages=[...])
    ctx.log_completion(
        model="gpt-4o",
        provider="openai",
        tokens_input=response.usage.prompt_tokens,
        tokens_output=response.usage.completion_tokens,
    )`,
      },
      {
        id: 'anthropic',
        categoryId: 'sdks',
        title: 'Anthropic',
        description: 'Track Claude 3.5 Sonnet, Opus, and Haiku with input/output token counts.',
        logo: <SiAnthropic className={logoCls} color="currentColor" />,
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
        categoryId: 'sdks',
        title: 'LangChain',
        description: 'Drop-in callback handler for LangChain chains, agents, and tools.',
        logo: <SiLangchain className={logoCls} color="currentColor" />,
        badge: { text: 'Python', variant: 'info' },
        language: 'python',
        code: `from cost_monitor import CostTracker
from cost_monitor._langchain import CMACallbackHandler

tracker = CostTracker(endpoint="YOUR_ENDPOINT", api_key="YOUR_API_KEY")
handler = CMACallbackHandler(tracker, agent_name="langchain-agent")

# Attach to any LangChain LLM or chain — callbacks propagate
llm = ChatOpenAI(model="gpt-4o", callbacks=[handler])
chain = prompt | llm | parser`,
      },
      {
        id: 'llamaindex',
        categoryId: 'sdks',
        title: 'LlamaIndex',
        description: 'Track LlamaIndex query-engine and retrieval pipeline costs.',
        logo: <Boxes className={logoCls} />,
        badge: { text: 'Python', variant: 'info' },
        language: 'python',
        code: `from cost_monitor import CostTracker

tracker = CostTracker(endpoint="YOUR_ENDPOINT", api_key="YOUR_API_KEY")

with tracker.trace_request(agent="llama-agent", workflow="query-engine") as ctx:
    response = query_engine.query("What is the revenue for Q4?")
    if hasattr(response, "metadata") and "token_count" in response.metadata:
        ctx.log_completion(
            model=response.metadata.get("model_name", "gpt-4o"),
            provider="openai",
            tokens_input=response.metadata.get("prompt_tokens", 0),
            tokens_output=response.metadata.get("completion_tokens", 0),
        )`,
      },
      {
        id: 'langfuse',
        categoryId: 'recipes',
        title: 'Langfuse',
        description: 'Run Langfuse alongside CMA — both track via LangChain callbacks.',
        logo: <Microscope className={logoCls} />,
        badge: { text: 'Export', variant: 'default' },
        language: 'python',
        code: `from langfuse.callback import CallbackHandler as LangfuseHandler
from cost_monitor._langchain import CMACallbackHandler
from cost_monitor import CostTracker

tracker = CostTracker(endpoint="YOUR_ENDPOINT", api_key="YOUR_API_KEY")
langfuse_handler = LangfuseHandler(public_key="pk-lf-...", secret_key="sk-lf-...")
cma_handler = CMACallbackHandler(tracker, agent_name="my-agent")

# Both handlers run side by side
llm = ChatOpenAI(model="gpt-4o", callbacks=[langfuse_handler, cma_handler])`,
      },
      {
        id: 'datadog',
        categoryId: 'recipes',
        title: 'Datadog',
        description: 'Push CMA cost metrics to Datadog via DogStatsD for dashboards and alerting.',
        logo: <SiDatadog className={logoCls} color="currentColor" />,
        badge: { text: 'Export', variant: 'default' },
        language: 'python',
        code: `from datadog import statsd
import httpx

def push_to_datadog(cma_endpoint: str, api_key: str):
    headers = {"Authorization": f"Bearer {api_key}"}
    r = httpx.get(f"{cma_endpoint}/api/v1/dashboard/overview?period=today", headers=headers)
    data = r.json()
    statsd.gauge("cma.total_cost", data["total_cost"], tags=["env:prod"])
    statsd.gauge("cma.total_requests", data["total_requests"], tags=["env:prod"])

push_to_datadog("YOUR_ENDPOINT", "YOUR_API_KEY")  # run on a schedule`,
      },
      {
        id: 'elastic',
        categoryId: 'recipes',
        title: 'Elasticsearch',
        description: 'Stream CMA events to Elasticsearch for Kibana dashboards and long-term search.',
        logo: <SiElasticsearch className={logoCls} color="currentColor" />,
        badge: { text: 'Export', variant: 'default' },
        language: 'python',
        code: `from elasticsearch import Elasticsearch
import httpx

es = Elasticsearch("http://localhost:9200")

def sync_to_elastic(cma_endpoint: str, api_key: str):
    headers = {"Authorization": f"Bearer {api_key}"}
    r = httpx.get(f"{cma_endpoint}/api/v1/events", params={"per_page": 100}, headers=headers)
    for event in r.json()["items"]:
        es.index(index="cma-events", id=event["id"], document=event)

sync_to_elastic("YOUR_ENDPOINT", "YOUR_API_KEY")  # run on a schedule`,
      },
      {
        id: 'prometheus',
        categoryId: 'recipes',
        title: 'Prometheus',
        description: 'Expose CMA metrics as a Prometheus scrape endpoint for Grafana.',
        logo: <SiPrometheus className={logoCls} color="currentColor" />,
        badge: { text: 'Export', variant: 'default' },
        language: 'python',
        code: `from prometheus_client import Gauge, start_http_server
import httpx, time

cost_gauge = Gauge("cma_total_cost_usd", "Total LLM cost", ["period"])

def collect(cma_endpoint: str, api_key: str):
    headers = {"Authorization": f"Bearer {api_key}"}
    o = httpx.get(f"{cma_endpoint}/api/v1/dashboard/overview?period=today", headers=headers).json()
    cost_gauge.labels(period="today").set(o["total_cost"])

start_http_server(9091)
while True:
    collect("YOUR_ENDPOINT", "YOUR_API_KEY")
    time.sleep(30)`,
      },
      {
        id: 'rest-post',
        categoryId: 'api',
        title: 'Send Events (POST)',
        description: 'Ingest cost events over HTTP from any language — Python, Node, Go, or curl.',
        logo: <Send className={logoCls} />,
        badge: { text: 'Any Language', variant: 'success' },
        language: 'bash',
        code: `curl -X POST YOUR_ENDPOINT/api/v1/events \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "agent_name": "my-agent",
    "model": "gpt-4o",
    "provider": "openai",
    "tokens_input": 1500,
    "tokens_output": 500,
    "status": "success"
  }'

# Batch: send {"events": [ ... ]} or a bare [ ... ] array`,
      },
      {
        id: 'rest-query',
        categoryId: 'api',
        title: 'Query Data (GET)',
        description: 'Read cost data, agent stats, and dashboard metrics via the REST API.',
        logo: <Terminal className={logoCls} />,
        badge: { text: 'Any Language', variant: 'success' },
        language: 'bash',
        code: `# Dashboard overview
curl YOUR_ENDPOINT/api/v1/dashboard/overview?period=today \\
  -H "Authorization: Bearer YOUR_API_KEY"

# Query events with filters
curl "YOUR_ENDPOINT/api/v1/events?agent_name=my-agent&status=success&per_page=10" \\
  -H "Authorization: Bearer YOUR_API_KEY"`,
      },
    ],
    [],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return integrations.filter((it) => {
      if (activeCat !== 'all' && it.categoryId !== activeCat) return false;
      if (!q) return true;
      return (
        it.title.toLowerCase().includes(q) || it.description.toLowerCase().includes(q)
      );
    });
  }, [integrations, activeCat, query]);

  const receiving = (eventCount ?? 0) > 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <header>
        <p className="eyebrow">Counting House</p>
        <h1 className="font-display text-3xl font-semibold tracking-tight text-primary">
          Integrations
        </h1>
        <p className="mt-1 text-sm text-muted">
          Connect your agents and export your ledger to the platforms of your choosing.
        </p>
      </header>

      {/* Connect panel */}
      <div className="card-ledger p-6">
        <div className="mb-4 flex items-center gap-2">
          <Plug className="h-5 w-5 text-gold" />
          <h2 className="font-display text-base font-semibold text-primary">Connect</h2>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {/* Endpoint */}
          <div>
            <label className="label">API Endpoint</label>
            <div className="flex items-center gap-2">
              <div className="flex-1 truncate rounded-lg bg-ink-950 px-3 py-2.5 font-mono text-sm text-brand-300">
                {endpoint}
              </div>
              <CopyBtn text={endpoint} id="endpoint" copiedId={copiedId} onCopy={copyToClipboard} />
            </div>
          </div>

          {/* API key */}
          <div>
            <label className="label">
              API Key
              <span className="ml-2 font-normal normal-case text-muted">
                (masked — copy sends the full key)
              </span>
            </label>
            <div className="flex items-center gap-2">
              <div className="flex-1 truncate rounded-lg bg-ink-950 px-3 py-2.5 font-mono text-sm text-brand-300">
                {showApiKey ? apiKey || 'No API key found' : maskedKey}
              </div>
              <button
                onClick={() => setShowApiKey((v) => !v)}
                className="btn-secondary h-9 w-9 p-0"
                title={showApiKey ? 'Hide key' : 'Reveal key'}
                aria-label={showApiKey ? 'Hide key' : 'Reveal key'}
              >
                {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
              <CopyBtn text={apiKey} id="apikey" copiedId={copiedId} onCopy={copyToClipboard} />
            </div>
          </div>
        </div>

        {/* Live status */}
        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-token pt-4">
          <div className="flex items-center gap-2.5">
            <span className="relative flex h-2.5 w-2.5">
              {receiving && (
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-400 opacity-75" />
              )}
              <span
                className={`relative inline-flex h-2.5 w-2.5 rounded-full ${
                  receiving ? 'bg-brand-500' : 'bg-ink-400'
                }`}
              />
            </span>
            <span className="text-sm text-secondary">
              {statusLoading ? (
                'Checking the ledger…'
              ) : receiving ? (
                <>
                  <span className="font-medium text-primary">Receiving data</span>
                  <span className="numeral text-muted">
                    {' '}
                    · {formatNumber(eventCount)} events · last {timeAgo(lastEvent)}
                  </span>
                </>
              ) : (
                'No events yet — send one to confirm the connection.'
              )}
            </span>
          </div>

          <div className="flex items-center gap-3">
            {testMsg && (
              <span
                className={`text-xs ${
                  testState === 'err'
                    ? 'text-oxblood-600 dark:text-oxblood-300'
                    : 'text-brand-600 dark:text-brand-300'
                }`}
              >
                {testState === 'ok' ? '✓ ' : ''}
                {testMsg}
              </span>
            )}
            <button
              onClick={sendTestEvent}
              disabled={testState === 'sending'}
              className="btn-secondary gap-2 text-xs"
            >
              <CircleDot className="h-3.5 w-3.5" />
              {testState === 'sending' ? 'Sending…' : 'Send test event'}
            </button>
          </div>
        </div>
      </div>

      {/* Toolbar: tabs + search */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {[{ id: 'all', title: 'All' }, ...categories].map((c) => (
            <button
              key={c.id}
              onClick={() => setActiveCat(c.id)}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                activeCat === c.id
                  ? 'bg-brand-600 text-white shadow-subtle'
                  : 'border border-token bg-surface text-secondary hover:bg-surface-2 hover:text-primary'
              }`}
            >
              {c.title}
            </button>
          ))}
        </div>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search integrations…"
            className="input w-full py-2 pl-9 text-sm sm:w-64"
          />
        </div>
      </div>

      {/* Grid */}
      {filtered.length > 0 ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((it) => (
            <button
              key={it.id}
              onClick={() => setSelected(it)}
              className="card-hover group flex flex-col items-start p-5 text-left"
            >
              <div className="mb-3 flex w-full items-center justify-between">
                <span className="flex h-11 w-11 items-center justify-center rounded-lg bg-surface-2 text-primary ring-1 ring-inset ring-[color:var(--color-border)] transition-colors group-hover:text-gold">
                  {it.logo}
                </span>
                <Badge text={it.badge.text} variant={it.badge.variant} />
              </div>
              <h3 className="font-display text-base font-semibold text-primary">{it.title}</h3>
              <p className="mt-1 line-clamp-2 text-sm text-muted">{it.description}</p>
              <span className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-gold opacity-0 transition-opacity group-hover:opacity-100">
                View setup <ArrowRight className="h-3 w-3" />
              </span>
            </button>
          ))}
        </div>
      ) : (
        <div className="card p-12 text-center">
          <Search className="mx-auto mb-3 h-8 w-8 text-muted" />
          <p className="font-display text-base text-secondary">No integrations match "{query}".</p>
          <p className="mt-1 text-sm text-muted">Try a different term or switch tabs.</p>
        </div>
      )}

      {/* Code drawer */}
      <Drawer
        isOpen={selected !== null}
        onClose={() => setSelected(null)}
        title={selected?.title ?? ''}
        titleIcon={selected?.logo}
      >
        {selected && (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Badge text={selected.badge.text} variant={selected.badge.variant} />
              <span className="numeral text-xs text-muted">{selected.language}</span>
            </div>
            <p className="text-sm text-secondary">{selected.description}</p>
            <div className="overflow-hidden rounded-lg bg-ink-950">
              <div className="flex items-center justify-between border-b border-ink-800/60 bg-ink-900/60 px-4 py-2">
                <span className="font-mono text-xs text-muted">{selected.language}</span>
                <CopyBtn
                  text={inject(selected.code)}
                  id={`drawer-${selected.id}`}
                  copiedId={copiedId}
                  onCopy={copyToClipboard}
                />
              </div>
              <pre className="overflow-x-auto p-4">
                <code className="whitespace-pre font-mono text-xs leading-relaxed text-brand-300">
                  {injectMasked(selected.code)}
                </code>
              </pre>
            </div>
            <p className="text-xs text-muted">
              Endpoint and key are pre-filled from this deployment. Copy runs the real values.
            </p>
          </div>
        )}
      </Drawer>
    </div>
  );
}

// ── Copy button ───────────────────────────────────────────────────────────────

function CopyBtn({
  text,
  id,
  copiedId,
  onCopy,
}: {
  text: string;
  id: string;
  copiedId: string | null;
  onCopy: (text: string, id: string) => void;
}) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onCopy(text, id);
      }}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
        copiedId === id
          ? 'bg-brand-600 text-white'
          : 'bg-ink-800 text-ink-300 hover:bg-ink-700 hover:text-white'
      }`}
    >
      {copiedId === id ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
      {copiedId === id ? 'Copied!' : 'Copy'}
    </button>
  );
}
