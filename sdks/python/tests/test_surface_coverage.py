"""SDK-01: every instrumented OpenAI/Anthropic surface, sync and async,
records exactly one event with the provider-reported tokens."""

import asyncio
import time

import anthropic
import openai
import pytest
from pydantic import BaseModel

from cost_monitor import auto

MSG = [{"role": "user", "content": "hi"}]


class Reply(BaseModel):
    text: str


def _sync_cases(llm):
    oai = openai.OpenAI(base_url=f"{llm.url}/v1", api_key="k", max_retries=0)
    ant = anthropic.Anthropic(base_url=llm.url, api_key="k", max_retries=0)

    def consume(stream):
        for _ in stream:
            pass

    return {
        "openai.embeddings": (
            lambda: oai.embeddings.create(model="text-embedding-3-small", input="x"),
            "openai", (11, 0),
        ),
        "openai.responses": (
            lambda: oai.responses.create(model="gpt-4o", input="hi"),
            "openai", (13, 5),
        ),
        "openai.responses.stream=True": (
            lambda: consume(oai.responses.create(model="gpt-4o", input="hi", stream=True)),
            "openai", (13, 5),
        ),
        "openai.chat.parse": (
            lambda: oai.chat.completions.parse(model="gpt-4o", messages=MSG),
            "openai", (7, 3),
        ),
        "anthropic.beta.create": (
            lambda: ant.beta.messages.create(model="claude-x", max_tokens=5, messages=MSG),
            "anthropic", (7, 3),
        ),
        "anthropic.beta.stream": (
            lambda: _anth_stream(ant.beta.messages),
            "anthropic", (7, 3),
        ),
        "openai.responses.parse": (
            lambda: oai.responses.parse(model="gpt-4o", input="hi"),
            "openai", (13, 5),
        ),
        "anthropic.parse": (
            lambda: ant.messages.parse(model="claude-x", max_tokens=5, messages=MSG),
            "anthropic", (7, 3),
        ),
        "anthropic.beta.parse": (
            lambda: ant.beta.messages.parse(model="claude-x", max_tokens=5, messages=MSG),
            "anthropic", (7, 3),
        ),
    }


def _anth_stream(resource):
    with resource.stream(model="claude-x", max_tokens=5, messages=MSG) as s:
        s.get_final_message()


async def _async_cases(llm, name):
    oai = openai.AsyncOpenAI(base_url=f"{llm.url}/v1", api_key="k", max_retries=0)
    ant = anthropic.AsyncAnthropic(base_url=llm.url, api_key="k", max_retries=0)
    if name == "openai.embeddings":
        await oai.embeddings.create(model="text-embedding-3-small", input="x")
    elif name == "openai.responses":
        await oai.responses.create(model="gpt-4o", input="hi")
    elif name == "openai.responses.stream=True":
        async for _ in await oai.responses.create(model="gpt-4o", input="hi", stream=True):
            pass
    elif name == "openai.legacy.completions":
        await oai.completions.create(model="gpt-3.5-turbo-instruct", prompt="hi")
    elif name == "anthropic.stream":
        async with ant.messages.stream(model="claude-x", max_tokens=5, messages=MSG) as s:
            await s.get_final_message()
    elif name == "anthropic.beta.create":
        await ant.beta.messages.create(model="claude-x", max_tokens=5, messages=MSG)
    elif name == "openai.chat.parse":
        await oai.chat.completions.parse(model="gpt-4o", messages=MSG)
    elif name == "openai.responses.parse":
        await oai.responses.parse(model="gpt-4o", input="hi")
    elif name == "anthropic.parse":
        await ant.messages.parse(model="claude-x", max_tokens=5, messages=MSG)
    elif name == "anthropic.beta.parse":
        await ant.beta.messages.parse(model="claude-x", max_tokens=5, messages=MSG)
    elif name == "anthropic.beta.stream":
        async with ant.beta.messages.stream(
            model="claude-x", max_tokens=5, messages=MSG
        ) as s:
            await s.get_final_message()
    else:
        raise AssertionError(f"unknown case {name}")


def _one_event(backend, provider):
    backend.wait_for(
        lambda b: any(e["provider"] == provider for e in b.delivered_events()), 3.0
    )
    # auto.stop() has already flushed the queue, so a duplicate would be in
    # the same delivery; the short settle guards against a late second batch.
    time.sleep(0.2)
    return [e for e in backend.delivered_events() if e["provider"] == provider]


@pytest.mark.parametrize(
    "name",
    [
        "openai.embeddings",
        "openai.responses",
        "openai.responses.stream=True",
        "openai.chat.parse",
        "anthropic.beta.create",
        "anthropic.beta.stream",
        "openai.responses.parse",
        "anthropic.parse",
        "anthropic.beta.parse",
    ],
)
def test_sync_surface_records_one_event(fake_backend, fake_llm, name):
    call, provider, tokens = _sync_cases(fake_llm)[name]
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        call()
    finally:
        auto.stop()
    events = _one_event(fake_backend, provider)
    assert len(events) == 1, events
    assert (events[0]["tokens_input"], events[0]["tokens_output"]) == tokens
    assert events[0]["status"] == "success"


@pytest.mark.parametrize(
    "name,provider,tokens",
    [
        ("openai.embeddings", "openai", (11, 0)),
        ("openai.responses", "openai", (13, 5)),
        ("openai.responses.stream=True", "openai", (13, 5)),
        ("openai.legacy.completions", "openai", (7, 3)),
        ("anthropic.stream", "anthropic", (7, 3)),
        ("anthropic.beta.create", "anthropic", (7, 3)),
        ("openai.chat.parse", "openai", (7, 3)),
        ("openai.responses.parse", "openai", (13, 5)),
        ("anthropic.parse", "anthropic", (7, 3)),
        ("anthropic.beta.parse", "anthropic", (7, 3)),
        ("anthropic.beta.stream", "anthropic", (7, 3)),
    ],
)
def test_async_surface_records_one_event(fake_backend, fake_llm, name, provider, tokens):
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        asyncio.run(_async_cases(fake_llm, name))
    finally:
        auto.stop()
    events = _one_event(fake_backend, provider)
    assert len(events) == 1, events
    assert (events[0]["tokens_input"], events[0]["tokens_output"]) == tokens


def test_responses_request_is_not_given_chat_stream_options(fake_backend, fake_llm):
    """The Responses API has no include_usage option; injecting it would 400."""
    oai = openai.OpenAI(base_url=f"{fake_llm.url}/v1", api_key="k", max_retries=0)
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        for _ in oai.responses.create(model="gpt-4o", input="hi", stream=True):
            pass
    finally:
        auto.stop()
    body = fake_llm.bodies("/responses")[-1]
    assert "include_usage" not in (body.get("stream_options") or {})


@pytest.mark.parametrize(
    "terminal,status", [("incomplete", "incomplete"), ("failed", "failure")]
)
@pytest.mark.parametrize("stream", [False, True])
def test_responses_terminal_status_is_kept_with_tokens(
    fake_backend, fake_llm, terminal, status, stream
):
    """Codex review on #24: an incomplete/failed Response carrying usage was
    recorded as success."""
    oai = openai.OpenAI(base_url=f"{fake_llm.url}/v1", api_key="k", max_retries=0)
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    try:
        result = oai.responses.create(
            model="gpt-4o", input="hi", stream=stream, metadata={"terminal": terminal}
        )
        if stream:
            for _ in result:
                pass
    finally:
        auto.stop()
    (event,) = _one_event(fake_backend, "openai")
    assert event["status"] == status
    assert (event["tokens_input"], event["tokens_output"]) == (13, 5)
