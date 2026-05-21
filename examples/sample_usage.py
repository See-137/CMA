"""Sample: How to instrument your LLM agents with CMA."""

import os

from cost_monitor import CostTracker

# Initialize tracker — API key is read from the environment so no credentials
# are embedded in source code. Set it before running:
#   $env:CMA_API_KEY = "your-key"  (PowerShell)
#   export CMA_API_KEY=your-key    (bash)
tracker = CostTracker(
    endpoint="http://localhost:8000",
    api_key=os.environ["CMA_API_KEY"],
    default_agent="research_agent",
)

# Example 1: Manual event logging
tracker.log_event(
    model="gpt-4o",
    provider="openai",
    tokens_input=1500,
    tokens_output=500,
    status="success",
    workflow="rag_pipeline",
)

# Example 2: Context manager with timing
with tracker.trace_request(agent="code_agent", workflow="code_review") as ctx:
    # ... your LLM call here ...
    ctx.log_completion(
        model="claude-3.5-sonnet",
        provider="anthropic",
        tokens_input=3000,
        tokens_output=1200,
    )

# Example 3: Zero-config auto-instrumentation (patches OpenAI/Anthropic/LangChain)
# from cost_monitor import auto_instrument
# tracker = auto_instrument(
#     endpoint="http://localhost:8000",
#     api_key=os.environ["CMA_API_KEY"],
# )
# # Now all openai.chat.completions.create calls are tracked automatically.
# client = OpenAI()
# response = client.chat.completions.create(model="gpt-4o", messages=[...])

tracker.close()
