"""Delivery reliability: one call is one stored event, whatever goes wrong."""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import os
import subprocess
import sys
import textwrap
import time

import anthropic
import openai
import pytest
from langchain_core.callbacks import CallbackManager
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from cost_monitor import attribution, auto
from cost_monitor._langchain import CMACallbackHandler
from cost_monitor._usage import usage_fields
from cost_monitor.tracker import CostTracker

MSG = [{"role": "user", "content": "hi"}]


def _tracker(url, **kw):
    kw.setdefault("flush_interval", 0.05)
    return CostTracker(endpoint=url, api_key="k", **kw)


def _log(tracker, **kw):
    tracker.log_event(model="m", provider="p", tokens_input=1, tokens_output=1, **kw)


# ACC-10 -------------------------------------------------------------------


def test_a_resent_event_keeps_its_id(fake_backend):
    fake_backend.script.extend(["hang:0.6"])
    tracker = _tracker(fake_backend.url, timeout=0.2, connect_timeout=0.2)
    _log(tracker)
    fake_backend.wait_for(lambda b: len(b.requests) >= 2, timeout=5)
    tracker.close()
    ids = [e["event_id"] for r in fake_backend.requests for e in r.events]
    assert len(ids) >= 2
    assert len(set(ids)) == 1


def test_separate_events_get_separate_ids(fake_backend):
    tracker = _tracker(fake_backend.url)
    _log(tracker)
    _log(tracker)
    tracker.close()
    ids = [e["event_id"] for e in _settled(fake_backend)]
    assert len(set(ids)) == 2


# SDK-04 / SDK-05 ----------------------------------------------------------


class _Fake(BaseChatModel):
    """A LangChain model built on a real (patched) OpenAI client."""

    base_url: str
    usage_metadata_value: dict | None = None
    use_client: bool = True

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _get_ls_params(self, stop=None, **kwargs):
        return {"ls_provider": "openai", "ls_model_name": "gpt-6.1-sol"}

    def _message(self) -> AIMessage:
        return AIMessage(content="x", usage_metadata=self.usage_metadata_value)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.use_client:
            client = openai.OpenAI(
                base_url=f"{self.base_url}/v1", api_key="k", max_retries=0
            )
            client.chat.completions.create(model="gpt-6.1-sol", messages=MSG)
        return ChatResult(generations=[ChatGeneration(message=self._message())])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.use_client:
            client = openai.AsyncOpenAI(
                base_url=f"{self.base_url}/v1", api_key="k", max_retries=0
            )
            await client.chat.completions.create(model="gpt-6.1-sol", messages=MSG)
        return ChatResult(generations=[ChatGeneration(message=self._message())])


def _events(backend, tracker):
    tracker.close()
    return _settled(backend)


def _settled(backend):
    """The server records a request just after answering it."""
    backend.wait_for(lambda b: b.delivered_events(), timeout=2)
    time.sleep(0.1)
    return backend.delivered_events()


def test_handler_plus_patched_client_is_one_event(fake_backend, fake_llm):
    tracker = auto.auto_instrument(fake_backend.url, "k", flush_interval=0.05)
    handler = CMACallbackHandler(tracker, agent_name="chain", workflow="wf")
    model = _Fake(base_url=fake_llm.url, callbacks=CallbackManager([handler]))
    model.invoke("hi")
    events = _events(fake_backend, tracker)
    assert len(events) == 1
    assert events[0]["agent_name"] == "chain"
    assert events[0]["workflow"] == "wf"


def test_async_handler_plus_patched_client_is_one_event(fake_backend, fake_llm):
    tracker = auto.auto_instrument(fake_backend.url, "k", flush_interval=0.05)
    handler = CMACallbackHandler(tracker, agent_name="chain")
    model = _Fake(base_url=fake_llm.url, callbacks=[handler])
    asyncio.run(model.ainvoke("hi"))
    events = _events(fake_backend, tracker)
    assert len(events) == 1
    assert events[0]["agent_name"] == "chain"


def test_handler_alone_reads_usage_metadata_and_ls_names(fake_backend):
    tracker = _tracker(fake_backend.url)
    handler = CMACallbackHandler(tracker)
    usage = {
        "input_tokens": 1000,
        "output_tokens": 50,
        "total_tokens": 1050,
        "input_token_details": {"cache_read": 700, "cache_creation": 100},
    }
    model = _Fake(
        base_url="http://unused",
        usage_metadata_value=usage,
        use_client=False,
        callbacks=[handler],
    )
    model.invoke("hi")
    [event] = _events(fake_backend, tracker)
    assert event["model"] == "gpt-6.1-sol"
    assert event["provider"] == "openai"
    # usage_metadata already counts cached input: nothing is added on top.
    assert event["tokens_input"] == 1000
    assert event["tokens_output"] == 50
    assert event["tokens_cache_read"] == 700
    assert event["tokens_cache_write"] == 100


def test_handler_failure_is_recorded_once(fake_backend):
    tracker = _tracker(fake_backend.url)
    handler = CMACallbackHandler(tracker)

    class Boom(_Fake):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("x" * 2000)

    with pytest.raises(RuntimeError):
        Boom(base_url="u", callbacks=[handler]).invoke("hi")
    [event] = _events(fake_backend, tracker)
    assert event["status"] == "failure"
    assert event["provider"] == "openai"
    assert len(event["metadata"]["error"]) == 500


def test_scope_is_restored_after_a_run(fake_backend):
    tracker = _tracker(fake_backend.url, default_agent="default")
    handler = CMACallbackHandler(tracker, agent_name="chain")
    _Fake(base_url="u", use_client=False, callbacks=[handler]).invoke("hi")
    _log(tracker)
    events = _events(fake_backend, tracker)
    assert [e["agent_name"] for e in events] == ["chain", "default"]


# SDK-06 -------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(os, "fork"), reason="no fork on this platform")
def test_forked_child_delivers_its_events_and_not_the_parents(fake_backend):
    tracker = _tracker(fake_backend.url, flush_interval=30)
    tracker.log_event(model="parent", provider="p")
    pid = os.fork()
    if pid == 0:
        status = 1
        try:
            tracker.log_event(model="child", provider="p")
            tracker.close()
            status = 0
        finally:
            os._exit(status)
    _, code = os.waitpid(pid, 0)
    assert code == 0
    fake_backend.wait_for(lambda b: len(b.delivered_events()) >= 1)
    assert [e["model"] for e in fake_backend.delivered_events()] == ["child"]
    tracker.close()
    time.sleep(0.2)
    assert sorted(e["model"] for e in fake_backend.delivered_events()) == [
        "child",
        "parent",
    ]


# SDK-07 -------------------------------------------------------------------


def test_one_bad_event_cannot_drop_the_others(fake_backend):
    tracker = _tracker(fake_backend.url, flush_interval=30)
    for i in range(10):
        tracker.log_event(model=f"good{i}", provider="p")
    tracker.log_event(
        model="odd",
        provider="p",
        cost=float("nan"),
        duration_ms=float("inf"),
        metadata={
            "when": dt.datetime(2026, 1, 1),
            "amount": decimal.Decimal("1.5"),
            "nan": float("nan"),
            "set": {1},
            "obj": object(),
            1: "int key",
        },
    )
    tracker.close()
    events = _settled(fake_backend)
    assert len(events) == 11
    odd = next(e for e in events if e["model"] == "odd")
    assert "cost" not in odd
    assert "duration_ms" not in odd
    assert odd["metadata"]["nan"] is None
    assert odd["metadata"]["when"].startswith("2026-01-01")


def test_metadata_is_copied_when_logged(fake_backend):
    tracker = _tracker(fake_backend.url, flush_interval=30)
    meta = {"a": 1}
    tracker.log_event(model="m", provider="p", metadata=meta)
    meta["a"] = 2
    tracker.close()
    assert _settled(fake_backend)[0]["metadata"] == {"a": 1}


def test_log_event_never_raises(fake_backend):
    tracker = _tracker(fake_backend.url)

    class Evil:
        def __str__(self):
            raise RuntimeError("no")

    tracker.log_event(model=Evil(), provider="p")  # type: ignore[arg-type]
    tracker.log_event(model="m", provider="p", metadata={"x": Evil()})
    tracker.close()
    assert tracker.stats["sent"] + tracker.stats["dropped"] == 2


# SDK-08 -------------------------------------------------------------------


def test_plain_tracker_flushes_at_interpreter_exit(fake_backend):
    code = textwrap.dedent(
        f"""
        from cost_monitor.tracker import CostTracker
        tracker = CostTracker(
            endpoint={fake_backend.url!r}, api_key="k", flush_interval=60
        )
        tracker.log_event(model="at-exit", provider="p")
        """
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=20)
    assert [e["model"] for e in _settled(fake_backend)] == ["at-exit"]


def test_close_stays_within_its_budget_against_a_hung_backend(fake_backend):
    fake_backend.script.extend(["hang:8"] * 5)
    tracker = _tracker(
        fake_backend.url, timeout=6, connect_timeout=1, close_timeout=2
    )
    _log(tracker)
    time.sleep(0.2)  # the worker is now stuck in the hung POST
    start = time.monotonic()
    tracker.close()
    assert time.monotonic() - start < 4


# SDK-09 -------------------------------------------------------------------


def test_413_splits_the_chunk_and_makes_progress(fake_backend):
    fake_backend.script.extend([413, 413])
    tracker = _tracker(fake_backend.url, flush_interval=30, batch_size=8)
    for i in range(8):
        tracker.log_event(model=f"m{i}", provider="p")
    tracker.close()
    assert len(_settled(fake_backend)) == 8
    assert tracker.stats["dropped"] == 0
    sizes = [len(r.events) for r in fake_backend.requests]
    assert sizes[:2] == [8, 4]
    assert max(sizes[2:]) == 2


def test_413_on_a_single_event_drops_only_that_event(fake_backend):
    fake_backend.script.extend([413])
    tracker = _tracker(fake_backend.url, flush_interval=30, batch_size=1)
    _log(tracker)
    deadline = time.monotonic() + 3
    while tracker.stats["dropped"] == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    _log(tracker)
    tracker.close()
    assert tracker.stats["dropped"] == 1
    assert len(_settled(fake_backend)) == 1


# SDK-10 -------------------------------------------------------------------


def test_attribution_reaches_patched_calls_and_budget_checks(fake_backend, fake_llm):
    tracker = auto.auto_instrument(
        fake_backend.url,
        "k",
        flush_interval=0.05,
        default_workflow="default-wf",
        enforce_budgets=True,
    )
    client = openai.OpenAI(base_url=f"{fake_llm.url}/v1", api_key="k", max_retries=0)
    client.chat.completions.create(model="gpt-4o", messages=MSG)
    with attribution(agent="researcher", swarm="s1", trace_id="t-1"):
        client.chat.completions.create(model="gpt-4o", messages=MSG)
        with tracker.context(workflow="inner"):
            client.chat.completions.create(model="gpt-4o", messages=MSG)
    first, second, third = _events(fake_backend, tracker)
    assert (first["agent_name"], first["workflow"]) == ("auto", "default-wf")
    assert second["agent_name"] == "researcher"
    assert second["swarm"] == "s1"
    assert second["trace_id"] == "t-1"
    assert (third["agent_name"], third["workflow"]) == ("researcher", "inner")
    assert any("agent_name=researcher" in q for q in fake_backend.budget_queries)


def test_trace_request_reaches_patched_calls(fake_backend, fake_llm):
    tracker = auto.auto_instrument(fake_backend.url, "k", flush_interval=0.05)
    client = openai.OpenAI(base_url=f"{fake_llm.url}/v1", api_key="k", max_retries=0)
    with tracker.trace_request(agent="traced", trace_id="t-2"):
        client.chat.completions.create(model="gpt-4o", messages=MSG)
    [event] = _events(fake_backend, tracker)
    assert (event["agent_name"], event["trace_id"]) == ("traced", "t-2")


def test_attribution_does_not_leak_between_async_tasks(fake_backend):
    tracker = _tracker(fake_backend.url)

    async def one(name):
        with attribution(agent=name):
            await asyncio.sleep(0.01)
            tracker.log_event(model=name, provider="p")

    async def main():
        await asyncio.gather(one("a"), one("b"))

    asyncio.run(main())
    tracker.close()
    for event in _settled(fake_backend):
        assert event["agent_name"] == event["model"]


# SDK-11 -------------------------------------------------------------------


def test_stats_reflect_what_the_server_stored(fake_backend):
    fake_backend.script.append({"processed": 1, "errored": 1, "duplicates": 1})
    tracker = _tracker(fake_backend.url, flush_interval=30)
    for _ in range(3):
        _log(tracker)
    tracker.close()
    stats = tracker.stats
    assert (stats["sent"], stats["rejected"], stats["duplicates"]) == (1, 1, 1)


# SDK-01 remainder ---------------------------------------------------------


def test_raw_response_records_tokens_and_returns_the_vendors_object(
    fake_backend, fake_llm
):
    tracker = auto.auto_instrument(fake_backend.url, "k", flush_interval=0.05)
    client = openai.OpenAI(base_url=f"{fake_llm.url}/v1", api_key="k", max_retries=0)
    raw = client.chat.completions.with_raw_response.create(model="gpt-4o", messages=MSG)
    assert raw.parse().choices  # caller's parse still works
    [event] = _events(fake_backend, tracker)
    assert event["status"] == "success"
    assert event["tokens_input"] > 0


def test_anthropic_raw_response_records_tokens(fake_backend, fake_llm):
    tracker = auto.auto_instrument(fake_backend.url, "k", flush_interval=0.05)
    client = anthropic.Anthropic(base_url=fake_llm.url, api_key="k", max_retries=0)
    raw = client.messages.with_raw_response.create(
        model="claude-sonnet-5", max_tokens=5, messages=MSG
    )
    assert raw.parse().content is not None
    [event] = _events(fake_backend, tracker)
    assert event["tokens_input"] > 0


def test_anthropic_async_raw_response_records_tokens(fake_backend, fake_llm):
    tracker = auto.auto_instrument(fake_backend.url, "k", flush_interval=0.05)

    async def main():
        client = anthropic.AsyncAnthropic(
            base_url=fake_llm.url, api_key="k", max_retries=0
        )
        raw = await client.messages.with_raw_response.create(
            model="claude-sonnet-5", max_tokens=5, messages=MSG
        )
        return await raw.parse()

    assert asyncio.run(main()).content is not None
    [event] = _events(fake_backend, tracker)
    assert event["tokens_input"] > 0


def test_async_raw_response_records_tokens(fake_backend, fake_llm):
    tracker = auto.auto_instrument(fake_backend.url, "k", flush_interval=0.05)

    async def main():
        client = openai.AsyncOpenAI(
            base_url=f"{fake_llm.url}/v1", api_key="k", max_retries=0
        )
        raw = await client.chat.completions.with_raw_response.create(
            model="gpt-4o", messages=MSG
        )
        return raw.parse()

    assert asyncio.run(main()).choices
    [event] = _events(fake_backend, tracker)
    assert event["tokens_input"] > 0


def test_streaming_response_is_untouched_and_recorded_as_unknown(
    fake_backend, fake_llm
):
    tracker = auto.auto_instrument(fake_backend.url, "k", flush_interval=0.05)
    client = openai.OpenAI(base_url=f"{fake_llm.url}/v1", api_key="k", max_retries=0)
    with client.chat.completions.with_streaming_response.create(
        model="gpt-4o", messages=MSG
    ) as response:
        assert response.parse().choices
    [event] = _events(fake_backend, tracker)
    assert event["status"] == "incomplete"
    assert event["tokens_input"] == 0


def test_raw_stream_gets_the_vendors_stream_and_no_injected_usage(
    fake_backend, fake_llm
):
    tracker = auto.auto_instrument(fake_backend.url, "k", flush_interval=0.05)
    client = openai.OpenAI(base_url=f"{fake_llm.url}/v1", api_key="k", max_retries=0)
    raw = client.chat.completions.with_raw_response.create(
        model="gpt-4o", messages=MSG, stream=True
    )
    stream = raw.parse()
    assert isinstance(stream, openai.Stream)
    for _ in stream:
        pass
    [event] = _events(fake_backend, tracker)
    assert event["status"] == "incomplete"


# OpenAI cache writes ------------------------------------------------------


def test_openai_cache_writes_are_captured():
    fields = usage_fields(
        {
            "input_tokens": 15000,
            "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": 12000, "cache_write_tokens": 3000},
        }
    )
    assert fields["tokens_input"] == 15000
    assert fields["tokens_cache_read"] == 12000
    assert fields["tokens_cache_write"] == 3000


def test_openai_cache_writes_cannot_exceed_uncached_input():
    fields = usage_fields(
        {
            "prompt_tokens": 100,
            "completion_tokens": 1,
            "prompt_tokens_details": {"cached_tokens": 90, "cache_write_tokens": 50},
        }
    )
    assert fields["tokens_cache_write"] == 10


def test_twelve_hour_ttl_prices_writes_at_the_long_rate(fake_backend, monkeypatch):
    from cost_monitor import _openai

    tracker = _tracker(fake_backend.url)
    usage = {
        "input_tokens": 100,
        "output_tokens": 1,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 80},
    }
    _openai._log_usage(tracker, "m", "t", time.time(), usage, "success", True)
    _openai._log_usage(tracker, "m", "t", time.time(), usage)
    long, short = _events(fake_backend, tracker)
    assert long["tokens_cache_write_1h"] == 80
    assert "tokens_cache_write_1h" not in short
    assert _openai._long_write_ttl({"prompt_cache_options": {"ttl": "12h"}})
    assert not _openai._long_write_ttl({"prompt_cache_options": {"ttl": "30m"}})


def test_unpriced_warning_fires_once_and_again_after_clearing(fake_backend, caplog):
    tracker = _tracker(fake_backend.url, budget_check_ttl=0)
    fake_backend.budget_response = {
        "allowed": True,
        "violations": [],
        "unpriced_events": 3,
    }
    with caplog.at_level("WARNING", logger="cost_monitor"):
        tracker.check_budget()
        tracker.check_budget()
        assert sum("no model price" in r.message for r in caplog.records) == 1
        fake_backend.budget_response = {"allowed": True, "violations": []}
        tracker.check_budget()
        fake_backend.budget_response = {
            "allowed": True,
            "violations": [],
            "unpriced_events": 1,
        }
        tracker.check_budget()
    assert sum("no model price" in r.message for r in caplog.records) == 2
    tracker.close()
