"""Streams must not change what user code receives, and must keep the
tokens the provider already reported when the caller stops early or its own
code raises (SDK-02, SDK-03)."""

import gc

import pytest

from cost_monitor import auto
from tests.test_instrumentation import (
    _anthropic_client,
    _events_for,
    _openai_client,
)

MSG = [{"role": "user", "content": "hi"}]


def _all_events(backend, provider, n=1):
    backend.wait_for(
        lambda b: len([e for e in b.delivered_events() if e["provider"] == provider])
        >= n,
        timeout=3.0,
    )
    return _events_for(backend, provider)


def test_injected_usage_chunk_is_not_shown_to_user_code(fake_backend, fake_llm):
    client = _openai_client(fake_llm)
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        # Typical code written for a stream that never asked for usage.
        texts = [
            ch.choices[0].delta.content
            for ch in client.chat.completions.create(
                model="gpt-4o", messages=MSG, stream=True
            )
        ]
    finally:
        auto.stop()
    assert texts  # no IndexError on the usage-only chunk
    (event,) = _all_events(fake_backend, "openai")
    assert (event["tokens_input"], event["tokens_output"]) == (7, 3)


def test_caller_requested_usage_chunk_is_still_delivered(fake_backend, fake_llm):
    client = _openai_client(fake_llm)
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        chunks = list(
            client.chat.completions.create(
                model="gpt-4o",
                messages=MSG,
                stream=True,
                stream_options={"include_usage": True},
            )
        )
    finally:
        auto.stop()
    assert chunks[-1].choices == [] and chunks[-1].usage is not None


def test_openai_early_break_is_incomplete_not_failure(fake_backend, fake_llm):
    client = _openai_client(fake_llm)
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        stream = client.chat.completions.create(model="gpt-4o", messages=MSG, stream=True)
        for _ in stream:
            break
        del stream
        gc.collect()
    finally:
        auto.stop()
    (event,) = _all_events(fake_backend, "openai")
    assert event["status"] == "incomplete"


def test_anthropic_early_break_keeps_input_tokens(fake_backend, fake_llm):
    client = _anthropic_client(fake_llm)
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        stream = client.messages.create(
            model="claude-x", max_tokens=5, messages=MSG, stream=True
        )
        for ev in stream:
            if ev.type == "content_block_delta":
                break
        del stream
        gc.collect()
    finally:
        auto.stop()
    (event,) = _all_events(fake_backend, "anthropic")
    assert event["status"] == "incomplete"
    assert event["tokens_input"] == 7


def test_user_exception_in_messages_stream_keeps_tokens(fake_backend, fake_llm):
    client = _anthropic_client(fake_llm)
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        with pytest.raises(KeyError):
            with client.messages.stream(
                model="claude-x", max_tokens=5, messages=MSG
            ) as stream:
                stream.get_final_message()
                raise KeyError("caller's own post-processing bug")
    finally:
        auto.stop()
    (event,) = _all_events(fake_backend, "anthropic")
    assert event["status"] == "incomplete"
    assert (event["tokens_input"], event["tokens_output"]) == (7, 3)
    assert "KeyError" in event["metadata"]["reason"]


# --- Re-review of PR #24: failures before/after message_start, part-read
# sessions, closing a proxy records immediately. ---------------------------


def _stream_kwargs(mode=None):
    kw = {"model": "claude-x", "max_tokens": 5, "messages": MSG}
    if mode:
        kw["metadata"] = {"user_id": mode}
    return kw


def test_api_error_before_message_start_is_recorded(fake_backend, fake_llm):
    import anthropic

    client = _anthropic_client(fake_llm)
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        with pytest.raises(anthropic.APIStatusError):
            with client.messages.stream(**_stream_kwargs("error_first")) as stream:
                stream.get_final_message()
    finally:
        auto.stop()
    (event,) = _all_events(fake_backend, "anthropic")
    assert event["status"] == "failure"
    assert (event["tokens_input"], event["tokens_output"]) == (0, 0)


def test_caller_raise_before_first_event_is_recorded(fake_backend, fake_llm):
    client = _anthropic_client(fake_llm)
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        with pytest.raises(KeyError):
            with client.messages.stream(**_stream_kwargs()):
                raise KeyError("before reading")
    finally:
        auto.stop()
    (event,) = _all_events(fake_backend, "anthropic")
    assert event["status"] == "incomplete"
    assert "KeyError" in event["metadata"]["reason"]


def test_async_api_error_before_message_start_is_recorded(fake_backend, fake_llm):
    import asyncio

    import anthropic

    async def run():
        client = anthropic.AsyncAnthropic(base_url=fake_llm.url, api_key="k", max_retries=0)
        async with client.messages.stream(**_stream_kwargs("error_first")) as stream:
            await stream.get_final_message()

    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        with pytest.raises(anthropic.APIStatusError):
            asyncio.run(run())
    finally:
        auto.stop()
    (event,) = _all_events(fake_backend, "anthropic")
    assert event["status"] == "failure"


def test_part_read_messages_stream_is_incomplete(fake_backend, fake_llm):
    client = _anthropic_client(fake_llm)
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        with client.messages.stream(**_stream_kwargs()) as stream:
            for ev in stream:
                if ev.type == "content_block_delta":
                    break
    finally:
        auto.stop()
    (event,) = _all_events(fake_backend, "anthropic")
    assert event["status"] == "incomplete"
    assert event["tokens_input"] == 7


def test_mid_stream_error_keeps_input_tokens(fake_backend, fake_llm):
    import anthropic

    client = _anthropic_client(fake_llm)
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        with pytest.raises(anthropic.APIStatusError):
            for _ in client.messages.create(**_stream_kwargs("error_mid"), stream=True):
                pass
    finally:
        auto.stop()
    (event,) = _all_events(fake_backend, "anthropic")
    assert event["status"] == "failure"
    assert event["tokens_input"] == 7


def test_with_block_break_records_without_garbage_collection(fake_backend, fake_llm):
    client = _openai_client(fake_llm)
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    keep = []
    try:
        with client.chat.completions.create(model="gpt-4o", messages=MSG, stream=True) as s:
            keep.append(s)  # a live reference: GC cannot be what records it
            for _ in s:
                break
        (event,) = _all_events(fake_backend, "openai")
    finally:
        auto.stop()
    assert event["status"] == "incomplete"


def test_cancellation_inside_the_stream_is_incomplete():
    """Task cancellation arrives while the wrapper awaits the next event."""
    import asyncio
    from types import SimpleNamespace

    from cost_monitor import _anthropic

    logged = []
    tracker = SimpleNamespace(log_event=lambda **kw: logged.append(kw))

    async def provider_stream():
        usage = SimpleNamespace(input_tokens=7)
        yield SimpleNamespace(type="message_start", message=SimpleNamespace(usage=usage))
        raise asyncio.CancelledError

    async def run():
        gen = _anthropic._wrap_async_stream(provider_stream(), tracker, "m", "t", 0.0)
        async for _ in gen:
            pass

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    (event,) = logged
    assert event["status"] == "incomplete"
    assert event["tokens_input"] == 7
