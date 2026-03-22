"""Sample: How to instrument your LLM agents with CMA."""

from cost_monitor import CostTracker

# Initialize tracker
tracker = CostTracker(
    endpoint="http://localhost:8000",
    api_key="your-api-key-here",
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

# Example 3: OpenAI integration
# from openai import OpenAI
# client = OpenAI()
# with tracker.trace_request(agent="assistant") as ctx:
#     response = client.chat.completions.create(model="gpt-4o", messages=[...])
#     ctx.log_completion(
#         model="gpt-4o",
#         provider="openai",
#         tokens_input=response.usage.prompt_tokens,
#         tokens_output=response.usage.completion_tokens,
#     )

tracker.close()
