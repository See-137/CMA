"""ACC-08/09: prompt-cache tokens reach CMA.

Anthropic reports cache reads/writes OUTSIDE input_tokens; CMA's tokens_input
counts all input, so the SDK adds them and sends the breakdown. OpenAI's
cached tokens are already inside prompt/input tokens; the SDK sends the
cached part so the backend can apply the cached-input price.
"""

import asyncio
import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import openai
import pytest

from cost_monitor import auto
from cost_monitor._langchain import CMACallbackHandler
from cost_monitor._usage import usage_fields

MSG = [{"role": "user", "content": "hi"}]

ANTHROPIC_CACHED_USAGE = {
    "input_tokens": 20,
    "cache_read_input_tokens": 50_000,
    "cache_creation_input_tokens": 500,
    "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 400},
    "output_tokens": 3,
}
EXPECTED_ANTHROPIC = {
    "tokens_input": 50_520,
    "tokens_output": 3,
    "tokens_cache_read": 50_000,
    "tokens_cache_write": 500,
    "tokens_cache_write_1h": 400,
}


def _server_module(fake_llm):
    # The module that actually serves fake_llm (pytest may import conftest
    # under a different name than ``tests.conftest``).
    return sys.modules[type(fake_llm).__module__]


def _event(backend, provider):
    backend.wait_for(
        lambda b: any(e["provider"] == provider for e in b.delivered_events()), 3.0
    )
    time.sleep(0.2)
    events = [e for e in backend.delivered_events() if e["provider"] == provider]
    assert len(events) == 1, events
    return events[0]


def _delivered(backend, n):
    backend.wait_for(lambda b: len(b.delivered_events()) >= n, 3.0)
    return backend.delivered_events()


def _subset(event, expected):
    return {k: event.get(k, 0) for k in expected}


def _cached_anthropic_sse(start_usage, delta_usage):
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_test", "type": "message", "role": "assistant",
            "model": "claude-sonnet-test", "content": [], "stop_reason": None,
            "stop_sequence": None, "usage": start_usage}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "hi"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": delta_usage}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(
        f"event: {n}\ndata: {json.dumps(p)}\n\n" for n, p in events
    ).encode()


def _run(backend, call, provider):
    auto.auto_instrument(endpoint=backend.url, api_key="k")
    try:
        call()
    finally:
        auto.stop()
    return _event(backend, provider)


# -- Anthropic ---------------------------------------------------------------


def test_anthropic_create_records_cache_tokens(fake_backend, fake_llm, monkeypatch):
    monkeypatch.setitem(
        _server_module(fake_llm)._ANTHROPIC_MESSAGE, "usage", ANTHROPIC_CACHED_USAGE
    )
    ant = anthropic.Anthropic(base_url=fake_llm.url, api_key="k", max_retries=0)
    ev = _run(
        fake_backend,
        lambda: ant.messages.create(model="claude-x", max_tokens=5, messages=MSG),
        "anthropic",
    )
    assert _subset(ev, EXPECTED_ANTHROPIC) == EXPECTED_ANTHROPIC


START = {k: v for k, v in ANTHROPIC_CACHED_USAGE.items() if k != "output_tokens"}
START["output_tokens"] = 0


def _patch_sse(monkeypatch, fake_llm, delta_usage):
    monkeypatch.setattr(
        _server_module(fake_llm),
        "_anthropic_sse",
        lambda: _cached_anthropic_sse(START, delta_usage),
    )


def test_anthropic_stream_true_records_cache_tokens(fake_backend, fake_llm, monkeypatch):
    _patch_sse(monkeypatch, fake_llm, {"output_tokens": 3})
    ant = anthropic.Anthropic(base_url=fake_llm.url, api_key="k", max_retries=0)

    def call():
        for _ in ant.messages.create(
            model="claude-x", max_tokens=5, messages=MSG, stream=True
        ):
            pass

    ev = _run(fake_backend, call, "anthropic")
    assert _subset(ev, EXPECTED_ANTHROPIC) == EXPECTED_ANTHROPIC


def test_anthropic_messages_stream_records_cache_tokens(
    fake_backend, fake_llm, monkeypatch
):
    _patch_sse(monkeypatch, fake_llm, {"output_tokens": 3})
    ant = anthropic.Anthropic(base_url=fake_llm.url, api_key="k", max_retries=0)

    def call():
        with ant.messages.stream(model="claude-x", max_tokens=5, messages=MSG) as s:
            s.get_final_message()

    ev = _run(fake_backend, call, "anthropic")
    assert _subset(ev, EXPECTED_ANTHROPIC) == EXPECTED_ANTHROPIC


def test_anthropic_stream_prefers_cumulative_delta_counts(
    fake_backend, fake_llm, monkeypatch
):
    """A message_delta carrying cache counts is cumulative and wins."""
    _patch_sse(
        monkeypatch,
        fake_llm,
        {"input_tokens": 30, "cache_read_input_tokens": 60_000,
         "cache_creation_input_tokens": 0, "output_tokens": 3},
    )
    ant = anthropic.Anthropic(base_url=fake_llm.url, api_key="k", max_retries=0)

    def call():
        for _ in ant.messages.create(
            model="claude-x", max_tokens=5, messages=MSG, stream=True
        ):
            pass

    ev = _run(fake_backend, call, "anthropic")
    assert ev["tokens_input"] == 60_030
    assert ev["tokens_cache_read"] == 60_000
    assert "tokens_cache_write" not in ev  # zero counts are not sent


def test_anthropic_async_stream_records_cache_tokens(
    fake_backend, fake_llm, monkeypatch
):
    _patch_sse(monkeypatch, fake_llm, {"output_tokens": 3})
    ant = anthropic.AsyncAnthropic(base_url=fake_llm.url, api_key="k", max_retries=0)

    async def call():
        stream = await ant.messages.create(
            model="claude-x", max_tokens=5, messages=MSG, stream=True
        )
        async for _ in stream:
            pass

    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        asyncio.run(call())
    finally:
        auto.stop()
    ev = _event(fake_backend, "anthropic")
    assert _subset(ev, EXPECTED_ANTHROPIC) == EXPECTED_ANTHROPIC


# -- OpenAI ------------------------------------------------------------------


def test_openai_chat_records_cached_input(fake_backend, fake_llm, monkeypatch):
    monkeypatch.setitem(
        _server_module(fake_llm)._OPENAI_COMPLETION,
        "usage",
        {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10,
         "prompt_tokens_details": {"cached_tokens": 4}},
    )
    oai = openai.OpenAI(base_url=f"{fake_llm.url}/v1", api_key="k", max_retries=0)
    ev = _run(
        fake_backend,
        lambda: oai.chat.completions.create(model="gpt-4o", messages=MSG),
        "openai",
    )
    assert (ev["tokens_input"], ev["tokens_output"]) == (7, 3)
    assert ev["tokens_cache_read"] == 4
    assert "tokens_cache_write" not in ev


# -- Normalisation and delivery ---------------------------------------------


def test_usage_fields_shapes():
    assert usage_fields(ANTHROPIC_CACHED_USAGE) == EXPECTED_ANTHROPIC
    # Anthropic objects with no cache info (None) are input-only.
    old = SimpleNamespace(
        input_tokens=9, output_tokens=2,
        cache_read_input_tokens=None, cache_creation_input_tokens=None,
    )
    assert usage_fields(old)["tokens_input"] == 9
    responses = SimpleNamespace(
        input_tokens=100, output_tokens=5,
        input_tokens_details=SimpleNamespace(cached_tokens=64),
    )
    f = usage_fields(responses)
    assert (f["tokens_input"], f["tokens_cache_read"]) == (100, 64)
    # Cached count larger than the input it belongs to is capped.
    bad = {"prompt_tokens": 5, "prompt_tokens_details": {"cached_tokens": 9}}
    assert usage_fields(bad)["tokens_cache_read"] == 5
    # Mocks and unknown shapes never invent tokens.
    assert usage_fields(MagicMock())["tokens_cache_read"] == 0
    assert usage_fields(None)["tokens_input"] == 0


def test_log_event_clamps_inconsistent_cache_counts(fake_backend):
    """The backend 422s an envelope with cache counts above the input, so the
    SDK clamps them rather than lose every event in the batch."""
    from cost_monitor import CostTracker

    tracker = CostTracker(endpoint=fake_backend.url, api_key="k")
    try:
        tracker.log_event(
            model="m", provider="p", tokens_input=10,
            tokens_cache_read=8, tokens_cache_write=8, tokens_cache_write_1h=50,
        )
        tracker.log_event(model="m2", provider="p", tokens_input=10)
    finally:
        tracker.close()
    events = {e["model"]: e for e in _delivered(fake_backend, 2)}
    assert (
        events["m"]["tokens_cache_read"],
        events["m"]["tokens_cache_write"],
        events["m"]["tokens_cache_write_1h"],
    ) == (8, 2, 2)
    assert "tokens_cache_read" not in events["m2"]


def test_log_completion_reads_anthropic_cache(fake_backend):
    from cost_monitor import CostTracker

    tracker = CostTracker(endpoint=fake_backend.url, api_key="k")
    try:
        tracker.log_completion(ANTHROPIC_CACHED_USAGE, model="c", provider="anthropic")
    finally:
        tracker.close()
    (ev,) = _delivered(fake_backend, 1)
    assert _subset(ev, EXPECTED_ANTHROPIC) == EXPECTED_ANTHROPIC


def test_langchain_handler_reads_anthropic_cache(fake_backend):
    from cost_monitor import CostTracker

    tracker = CostTracker(endpoint=fake_backend.url, api_key="k")
    handler = CMACallbackHandler(tracker)
    try:
        handler.on_llm_start({"kwargs": {"model": "claude-x"}}, ["hi"], run_id="r1")
        handler.on_llm_end(
            SimpleNamespace(llm_output={"token_usage": ANTHROPIC_CACHED_USAGE}),
            run_id="r1",
        )
    finally:
        tracker.close()
    (ev,) = _delivered(fake_backend, 1)
    assert _subset(ev, EXPECTED_ANTHROPIC) == EXPECTED_ANTHROPIC
