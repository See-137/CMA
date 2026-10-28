# CMA Cost Monitor — Python SDK

Track LLM costs across multi-agent systems by sending events to a [CMA](https://github.com/See-137/CMA) backend.

## Installation

```bash
pip install cma-cost-monitor
```

With optional provider extras:

```bash
pip install cma-cost-monitor[openai]
pip install cma-cost-monitor[anthropic]
```

## Quick Start

### Basic event logging

```python
from cost_monitor import CostTracker

tracker = CostTracker(
    endpoint="http://localhost:8000",
    api_key="your-api-key",
    default_agent="research_agent",
)

tracker.log_event(
    model="gpt-4o",
    provider="openai",
    tokens_input=1500,
    tokens_output=500,
    workflow="rag_pipeline",
)

tracker.close()
```

### Context manager with automatic timing

```python
with CostTracker(endpoint="http://localhost:8000", api_key="key") as tracker:
    with tracker.trace_request(agent="summarizer", workflow="digest") as ctx:
        # ... your LLM call here ...
        ctx.log_completion(
            model="claude-3.5-sonnet",
            provider="anthropic",
            tokens_input=3000,
            tokens_output=1200,
        )
```

### OpenAI integration

```python
from openai import OpenAI
from cost_monitor import CostTracker

client = OpenAI()

with CostTracker(endpoint="http://localhost:8000", api_key="key") as tracker:
    with tracker.trace_request(agent="assistant") as ctx:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello"}],
        )
        ctx.log_completion(
            model="gpt-4o",
            provider="openai",
            tokens_input=response.usage.prompt_tokens,
            tokens_output=response.usage.completion_tokens,
        )

    # Or use the convenience method:
    tracker.log_completion(
        response_usage=response.usage,
        model="gpt-4o",
        provider="openai",
        agent="assistant",
    )
```

## Configuration

| Parameter | Default | Description |
|---|---|---|
| `endpoint` | *(required)* | CMA backend URL |
| `api_key` | *(required)* | API key from CMA setup |
| `default_agent` | `None` | Default agent name for all events |
| `default_workflow` | `None` | Default workflow tag |
| `default_swarm` | `None` | Default swarm tag |
| `flush_interval` | `5.0` | Seconds between background flushes |
| `batch_size` | `50` | Buffer size that triggers an immediate flush |
| `budget_check_ttl` | `10.0` | Seconds a budget-check result is cached per attribution |
| `budget_check_timeout` | `2.0` | Timeout of the budget-check request (an uncached check blocks the caller at most this long) |
| `budget_fail_closed` | `False` | Treat an unreachable CMA as "budget exhausted" |

## What auto-instrumentation records

| Library | Calls (sync and async) |
|---|---|
| OpenAI | `chat.completions.create` (incl. `stream=True`, `.stream()` helper), `chat.completions.parse`, legacy `completions.create`, `embeddings.create`, `responses.create` (incl. `stream=True`), `responses.parse` |
| Anthropic | `messages.create` (incl. `stream=True`), `messages.parse`, `messages.stream`, and the same under `beta.messages` |

Not yet recorded: `with_raw_response` / `with_streaming_response` variants (a
streamed `with_raw_response` call also returns CMA's stream proxy rather than the
SDK's raw-response object), resuming a Responses stream by `response_id`
(`responses.stream(response_id=...)` / `retrieve`) (tracked as SDK-01 remainder
in `docs/FINDINGS.md`).

Prompt-cache tokens are recorded on every surface above: Anthropic cache reads and
writes (5-minute and 1-hour) are added to `tokens_input` and sent as
`tokens_cache_read` / `tokens_cache_write` / `tokens_cache_write_1h`; OpenAI's
`cached_tokens` is sent as `tokens_cache_read`. The backend prices them at the
model's cache prices. `log_event`, `log_completion` and the LangChain handler take
the same fields; `log_event` clamps cache counts that exceed `tokens_input` (the
backend would reject the whole batch).

**Upgrade the CMA server first.** This SDK needs a server with migration 006. An
older server ignores the cache fields but receives the larger `tokens_input`, so it
bills cache reads at the full input price (up to 10× too much for Anthropic cache
hits). The SDK cannot detect the server version. `responses.stream()`
for a new request is recorded: it goes through `responses.create`.

Streams are passed through unchanged. If CMA injects
`stream_options.include_usage` for an OpenAI chat stream, the extra usage-only
chunk is consumed by CMA, not yielded to your code. A stream you stop reading early, close
unread, or cancel (task cancellation, Ctrl-C) is recorded as `status="incomplete"` with
whatever tokens the provider already reported, at the moment you close it or leave its
`with` block; an exception raised by your own code inside `messages.stream()` does not turn
billed tokens into a `failure`. One exception: OpenAI's `chat.completions.stream()` /
`responses.stream()` helpers close the HTTP response directly, so a helper stream left
part-read is recorded at the next cyclic garbage collection rather than at block exit (not
deterministic; it can be lost at interpreter exit). A stream dropped without being read or
closed is not recorded. For async `messages.stream()`, argument errors are raised when the
block is entered rather than at the call.

## Budget enforcement

CMA always records spend. Enforcement happens **before** a call: budgets whose
control action is anything other than *Alert only* (e.g. *Block new calls*)
refuse new calls once exhausted.

```python
from cost_monitor import BudgetExceeded, auto_instrument

auto_instrument(endpoint="http://localhost:8000", api_key="...",
                default_agent="research_agent", enforce_budgets=True)

try:
    client.chat.completions.create(model="gpt-4o", messages=[...])
except BudgetExceeded as exc:
    print(exc.violations)   # which budget, limit, spent
```

Manual use: `tracker.enforce_budget(agent=..., workflow=..., swarm=...)` raises
`BudgetExceeded`; `tracker.check_budget(...)` returns
`{"allowed": bool, "violations": [...]}` without raising.

Guarantees and limits:

- Global budgets always apply; agent/swarm/workflow budgets apply only when the
  call carries that attribute (the tracker defaults).
- Results are cached for `budget_check_ttl` seconds, and concurrent callers are
  not serialised, so spend can overshoot a limit by the calls made within one TTL.
  This is a soft limit, not a hard stop.
- If CMA is unreachable, calls proceed (fail-open) unless `budget_fail_closed=True`;
  a failed check is cached for at most 1 s so a blip doesn't lock calls out for a TTL.
- One backend check per attribution per TTL, however many threads call concurrently.
- Spend counts toward the budget period its timestamp falls in; a replayed
  (back-dated) event never makes today's budget look exhausted.
- Enforcement applies to every auto-instrumented path (table above). LangChain
  callbacks are not enforced.

## Design Notes

- **Thread-safe**: The event buffer is protected by a lock. Safe to share a single tracker across threads.
- **Non-blocking**: Events are buffered and flushed in a background daemon thread. `log_event` returns immediately.
- **Graceful failures**: Network errors are logged to stderr and never raised. Events are re-buffered on connection failures for retry on the next flush cycle.
