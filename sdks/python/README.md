# CMA Cost Monitor — Python SDK

Track LLM costs across multi-agent systems by sending events to a [CMA](https://github.com/your-org/cma) backend.

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

## Design Notes

- **Thread-safe**: The event buffer is protected by a lock. Safe to share a single tracker across threads.
- **Non-blocking**: Events are buffered and flushed in a background daemon thread. `log_event` returns immediately.
- **Graceful failures**: Network errors are logged to stderr and never raised. Events are re-buffered on connection failures for retry on the next flush cycle.
