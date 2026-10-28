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


# --- Second re-review of PR #24 -------------------------------------------


class _Recorder:
    def __init__(self):
        self.events = []

    def log_event(self, **kw):
        self.events.append(kw)


def test_async_close_from_another_task_closes_the_sdk_stream(caplog):
    """close() while another task awaits the next event must not raise and
    must still close the SDK stream (previously aclose() raised
    'asynchronous generator is already running')."""
    import asyncio
    from types import SimpleNamespace

    from cost_monitor import _anthropic
    from cost_monitor._patching import InstrumentedAsyncStream, StreamState

    rec = _Recorder()

    class FakeSDKStream:
        def __init__(self):
            self.closed = asyncio.Event()

        def __aiter__(self):
            return self._events()

        async def _events(self):
            usage = SimpleNamespace(input_tokens=7)
            yield SimpleNamespace(type="message_start", message=SimpleNamespace(usage=usage))
            await self.closed.wait()
            raise RuntimeError("stream closed")

        async def close(self):
            self.closed.set()

    async def run():
        sdk = FakeSDKStream()
        state = StreamState()
        gen = _anthropic._wrap_async_stream(sdk, rec, "m", "t", 0.0, state)
        proxy = InstrumentedAsyncStream(sdk, gen, state)

        async def consume():
            async for _ in proxy:
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)  # consumer is now waiting inside __anext__
        await proxy.close()
        assert sdk.closed.is_set()
        with pytest.raises(RuntimeError, match="stream closed"):
            await asyncio.wait_for(task, 1.0)

    asyncio.run(run())
    (event,) = rec.events
    assert event["status"] == "incomplete"  # the caller's own close
    assert event["tokens_input"] == 7
    assert "failed to record a closed stream" not in caplog.text


def test_sync_close_from_another_thread_closes_the_sdk_stream(caplog):
    import threading
    from types import SimpleNamespace

    from cost_monitor import _anthropic
    from cost_monitor._patching import InstrumentedStream, StreamState

    rec = _Recorder()
    closed = threading.Event()
    reading = threading.Event()

    class FakeSDKStream:
        def __iter__(self):
            usage = SimpleNamespace(input_tokens=7)
            yield SimpleNamespace(type="message_start", message=SimpleNamespace(usage=usage))
            reading.set()
            closed.wait(2)
            raise RuntimeError("stream closed")

        def close(self):
            closed.set()

    sdk = FakeSDKStream()
    state = StreamState()
    proxy = InstrumentedStream(
        sdk, _anthropic._wrap_sync_stream(sdk, rec, "m", "t", 0.0, state), state
    )
    errors = []

    def consume():
        try:
            for _ in proxy:
                pass
        except RuntimeError as exc:
            errors.append(exc)

    t = threading.Thread(target=consume)
    t.start()
    assert reading.wait(2)
    proxy.close()  # must not raise 'generator already executing'
    t.join(2)
    assert closed.is_set() and errors
    (event,) = rec.events
    assert event["status"] == "incomplete" and event["tokens_input"] == 7
    assert "failed to record a closed stream" not in caplog.text


def test_streams_closed_unread_are_recorded(fake_backend, fake_llm):
    oai = _openai_client(fake_llm)
    ant = _anthropic_client(fake_llm)
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        with oai.chat.completions.create(model="gpt-4o", messages=MSG, stream=True):
            pass
        ant.messages.create(**_stream_kwargs(), stream=True).close()
        with ant.messages.stream(**_stream_kwargs()):
            pass
    finally:
        auto.stop()
    events = _all_events(fake_backend, "openai") + _all_events(fake_backend, "anthropic", 2)
    assert len(events) == 3
    assert {e["status"] for e in events} == {"incomplete"}


def test_async_exit_records_a_part_read_stream(fake_backend, fake_llm):
    """__aexit__ must record immediately — checked inside the event loop,
    before asyncio.run's shutdown would finalise the generator anyway."""
    import asyncio

    import openai

    from cost_monitor._patching import get_active_tracker

    logged = []

    async def run():
        client = openai.AsyncOpenAI(base_url=f"{fake_llm.url}/v1", api_key="k", max_retries=0)
        keep = []
        async with await client.chat.completions.create(
            model="gpt-4o", messages=MSG, stream=True
        ) as s:
            keep.append(s)
            async for _ in s:
                break
        assert [e["status"] for e in logged] == ["incomplete"]

    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    tracker = get_active_tracker()
    original = tracker.log_event

    def spy(**kw):
        logged.append(kw)
        return original(**kw)

    tracker.log_event = spy
    try:
        asyncio.run(run())
    finally:
        auto.stop()


def test_async_mid_stream_error_keeps_input_tokens(fake_backend, fake_llm):
    import asyncio

    import anthropic

    async def run():
        client = anthropic.AsyncAnthropic(base_url=fake_llm.url, api_key="k", max_retries=0)
        async for _ in await client.messages.create(**_stream_kwargs("error_mid"), stream=True):
            pass

    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        with pytest.raises(anthropic.APIStatusError):
            asyncio.run(run())
    finally:
        auto.stop()
    (event,) = _all_events(fake_backend, "anthropic")
    assert event["status"] == "failure" and event["tokens_input"] == 7


def test_openai_cancellation_inside_the_stream_is_incomplete():
    import asyncio
    from types import SimpleNamespace

    from cost_monitor import _openai

    rec = _Recorder()

    async def provider():
        yield SimpleNamespace(usage=None, choices=[1])
        raise asyncio.CancelledError

    async def run():
        async for _ in _openai._wrap_async_stream(provider(), rec, "m", "t", 0.0):
            pass

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    (event,) = rec.events
    assert event["status"] == "incomplete"


@pytest.mark.parametrize("module", ["openai", "anthropic"])
def test_cancellation_while_waiting_for_the_response_is_incomplete(module):
    import asyncio

    from cost_monitor import _anthropic, _openai
    from cost_monitor._patching import set_active_tracker

    rec = _Recorder()

    async def never(self, *a, **k):
        await asyncio.sleep(10)

    patched = (
        _openai._make_async_wrapper(never)
        if module == "openai"
        else _anthropic._make_acreate(never)
    )

    async def run():
        task = asyncio.create_task(patched(None, model="m"))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    set_active_tracker(rec)
    try:
        asyncio.run(run())
    finally:
        set_active_tracker(None)
    (event,) = rec.events
    assert event["status"] == "incomplete"


def test_async_stream_argument_error_records_nothing_and_reuse_raises(
    fake_backend, fake_llm
):
    import asyncio

    import anthropic

    async def bad_args():
        client = anthropic.AsyncAnthropic(base_url=fake_llm.url, api_key="k", max_retries=0)
        async with client.messages.stream(**_stream_kwargs(), output_format={"x": 1}):
            pass

    async def reuse():
        client = anthropic.AsyncAnthropic(base_url=fake_llm.url, api_key="k", max_retries=0)
        mgr = client.messages.stream(**_stream_kwargs())
        async with mgr as s:
            await s.get_final_message()
        async with mgr:
            pass

    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        with pytest.raises(TypeError):
            asyncio.run(bad_args())
        with pytest.raises(RuntimeError, match="cannot be reused"):
            asyncio.run(reuse())
    finally:
        auto.stop()
    (event,) = _all_events(fake_backend, "anthropic")  # only the real call
    assert event["status"] == "success"
    assert len(fake_llm.bodies("/messages")) == 1
