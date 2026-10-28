"""Pre-call budget enforcement (decision D1): when enabled, an exhausted
blocking budget stops the call BEFORE the provider is contacted."""

import asyncio

import pytest

from cost_monitor import BudgetExceeded, CostTracker
from cost_monitor import auto
from tests.test_instrumentation import _anthropic_client, _chat, _openai_client

EXHAUSTED = {
    "allowed": False,
    "violations": [{"name": "cap", "message": "Budget 'cap' exhausted: $2.00 / $1.00"}],
}


def test_check_budget_sends_attribution_and_caches(fake_backend):
    with CostTracker(
        endpoint=fake_backend.url, api_key="k", default_agent="research"
    ) as t:
        assert t.check_budget()["allowed"] is True
        assert t.check_budget()["allowed"] is True  # served from cache
        t.check_budget(agent="writer", workflow="wf")
    assert fake_backend.budget_queries == [
        "agent_name=research",
        "agent_name=writer&workflow=wf",
    ]


def test_check_uses_the_attribution_events_are_recorded_under(fake_backend):
    """No default agent -> events are recorded as 'unknown'; the check must
    ask about 'unknown' too, and clip long names the same way."""
    with CostTracker(endpoint=fake_backend.url, api_key="k") as t:
        t.check_budget()
        t.check_budget(agent="a" * 300)
        t.log_event(model="m", provider="p", agent_name="a" * 300)
        t.flush()
    fake_backend.wait_for(lambda b: b.delivered_events(), timeout=2.0)
    recorded = fake_backend.delivered_events()[0]["agent_name"]
    assert fake_backend.budget_queries == [
        "agent_name=unknown",
        f"agent_name={recorded}",
    ]
    assert len(recorded) == 255


def test_enforce_budget_raises_with_violations(fake_backend):
    fake_backend.budget_response = EXHAUSTED
    with CostTracker(endpoint=fake_backend.url, api_key="k") as t:
        with pytest.raises(BudgetExceeded) as exc:
            t.enforce_budget()
    assert exc.value.violations[0]["name"] == "cap"
    assert "cap" in str(exc.value)


def test_budget_check_fails_open_by_default_and_closed_on_request(fake_backend):
    fake_backend.budget_status = 503
    with CostTracker(endpoint=fake_backend.url, api_key="k") as t:
        result = t.check_budget()
        assert result["allowed"] is True and "error" in result
    with CostTracker(
        endpoint=fake_backend.url, api_key="k", budget_fail_closed=True
    ) as t:
        with pytest.raises(BudgetExceeded):
            t.enforce_budget()


def test_enforcement_is_off_by_default(fake_backend, fake_llm):
    fake_backend.budget_response = EXHAUSTED
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        _chat(_openai_client(fake_llm))  # does not raise
    finally:
        auto.stop()
    assert fake_backend.budget_queries == []
    assert len(fake_llm.bodies("/chat/completions")) == 1


def test_exhausted_budget_blocks_openai_and_anthropic_before_the_call(
    fake_backend, fake_llm
):
    fake_backend.budget_response = EXHAUSTED
    auto.auto_instrument(
        endpoint=fake_backend.url,
        api_key="k",
        default_agent="research",
        enforce_budgets=True,
    )
    try:
        with pytest.raises(BudgetExceeded):
            _chat(_openai_client(fake_llm))
        client = _anthropic_client(fake_llm)
        with pytest.raises(BudgetExceeded):
            client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}],
            )
        with pytest.raises(BudgetExceeded):
            client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}],
            )
    finally:
        tracker = auto.get_tracker()
        auto.stop()
    # The providers were never contacted and nothing was logged as a failure.
    assert fake_llm.bodies("/chat/completions") == []
    assert fake_llm.bodies("/messages") == []
    assert tracker.stats["sent"] == 0
    # Patched calls send the provider and model too, so provider/model-scoped
    # budgets can block them.
    assert fake_backend.budget_queries[0] == (
        "agent_name=research&provider=openai&model=gpt-4o"
    )


def test_async_call_is_blocked(fake_backend, fake_llm):
    import openai

    fake_backend.budget_response = EXHAUSTED
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k", enforce_budgets=True)

    async def call():
        client = openai.AsyncOpenAI(
            base_url=f"{fake_llm.url}/v1", api_key="test-key", max_retries=0
        )
        await client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )

    try:
        with pytest.raises(BudgetExceeded):
            asyncio.run(call())
    finally:
        auto.stop()
    assert fake_llm.bodies("/chat/completions") == []


def test_allowed_budget_lets_calls_through(fake_backend, fake_llm):
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k", enforce_budgets=True)
    try:
        _chat(_openai_client(fake_llm))
    finally:
        auto.stop()
    assert len(fake_llm.bodies("/chat/completions")) == 1


def test_concurrent_cold_cache_makes_one_backend_check(fake_backend):
    import threading

    fake_backend.budget_delay = 0.3
    with CostTracker(endpoint=fake_backend.url, api_key="k") as t:
        threads = [threading.Thread(target=t.check_budget) for _ in range(20)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    assert len(fake_backend.budget_queries) == 1


def test_ttl_expiry_rechecks_and_unblocks(fake_backend):
    import time

    fake_backend.budget_response = EXHAUSTED
    with CostTracker(endpoint=fake_backend.url, api_key="k", budget_check_ttl=0.2) as t:
        with pytest.raises(BudgetExceeded):
            t.enforce_budget()
        fake_backend.budget_response = {"allowed": True, "violations": []}
        with pytest.raises(BudgetExceeded):
            t.enforce_budget()  # still cached
        time.sleep(0.25)
        t.enforce_budget()  # fresh check: allowed again
    assert len(fake_backend.budget_queries) == 2


def test_failed_check_is_cached_only_briefly(fake_backend):
    import time

    fake_backend.budget_status = 503
    with CostTracker(
        endpoint=fake_backend.url, api_key="k", budget_fail_closed=True, budget_check_ttl=60
    ) as t:
        with pytest.raises(BudgetExceeded):
            t.enforce_budget()
        fake_backend.budget_status = 200
        time.sleep(1.05)
        t.enforce_budget()  # the blip did not lock calls out for the full TTL


def test_streaming_and_async_calls_are_blocked(fake_backend, fake_llm):
    import anthropic
    import openai

    fake_backend.budget_response = EXHAUSTED
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k", enforce_budgets=True)

    async def async_calls():
        oai = openai.AsyncOpenAI(base_url=f"{fake_llm.url}/v1", api_key="k", max_retries=0)
        ant = anthropic.AsyncAnthropic(base_url=fake_llm.url, api_key="k", max_retries=0)
        with pytest.raises(BudgetExceeded):
            await oai.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": "hi"}], stream=True
            )
        with pytest.raises(BudgetExceeded):
            await ant.messages.create(
                model="claude-x", max_tokens=5, messages=[{"role": "user", "content": "hi"}]
            )

    try:
        with pytest.raises(BudgetExceeded):
            _openai_client(fake_llm).chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": "hi"}], stream=True
            )
        asyncio.run(async_calls())
    finally:
        auto.stop()
    assert fake_llm.bodies("/chat/completions") == []
    assert fake_llm.bodies("/messages") == []
