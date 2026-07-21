"""Patching-hygiene spec for auto-instrumentation (PR 2 target behavior).

TDD red-phase: encodes the post-rebuild contract for _openai.py /
_anthropic.py / auto.py. Current failures, each a live defect:

  1. Repeated auto_instrument() stacks wrappers (patch captures whatever
     create currently is — including the previous wrapper), and there is no
     uninstrument()/unpatch API at all.
  2. auto.stop() leaves patches installed, pointing at a closed tracker.
  3. Patched streaming create() returns a bare generator — the SDK's Stream
     object contract (context manager, .close(), .response) is broken.
  4. Anthropic's recommended streaming API, client.messages.stream(...),
     is not instrumented at all (it does not route through Messages.create).
  5. stream_options={"include_usage": True} is force-injected into OpenAI
     requests with no opt-out — a request mutation that breaks Azure
     OpenAI api-versions that reject stream_options.

All calls go to the fake_llm fixture (real openai/anthropic SDKs pointed at
a local server speaking their wire formats: 7 input / 3 output tokens in
every response); CMA events are asserted via the fake_backend fixture.
The pristine-restore fixture snapshots the un-patched SDK methods at import
time and restores them after every test — patching is process-global and
must never leak between tests.
"""

from __future__ import annotations

import pytest

import cost_monitor.auto as auto

# Pristine originals, captured at import time — before any test patches.
# name -> (owner, attr, pristine). This is the COMPLETE set of attributes
# patch_openai()/patch_anthropic() touch; the fixture below both restores
# and leak-checks against it, so a new patch target that isn't added here
# fails loudly instead of silently leaking across tests.
from openai.resources import completions as _oai_legacy
from openai.resources.chat import completions as _oai_chat

from anthropic.resources import messages as _anth_messages

_PRISTINE = {
    "oai_sync": (_oai_chat.Completions, "create", _oai_chat.Completions.create),
    "oai_async": (
        _oai_chat.AsyncCompletions,
        "create",
        _oai_chat.AsyncCompletions.create,
    ),
    "oai_legacy": (_oai_legacy.Completions, "create", _oai_legacy.Completions.create),
    "anth_sync": (_anth_messages.Messages, "create", _anth_messages.Messages.create),
    "anth_async": (
        _anth_messages.AsyncMessages,
        "create",
        _anth_messages.AsyncMessages.create,
    ),
    "anth_stream": (_anth_messages.Messages, "stream", _anth_messages.Messages.stream),
}


def _pristine(name: str):
    return _PRISTINE[name][2]


@pytest.fixture(autouse=True)
def restore_llm_sdks():
    """Kill any live tracker, then verify stop() restored every pristine
    method — restoring manually only as a last resort so one leaky test
    can't poison the rest of the suite."""
    yield
    auto.stop()
    leaked = [
        name
        for name, (owner, attr, pristine) in _PRISTINE.items()
        if getattr(owner, attr) is not pristine
    ]
    for owner, attr, pristine in _PRISTINE.values():
        setattr(owner, attr, pristine)
    assert not leaked, f"auto.stop() left patches installed: {leaked}"


def _openai_client(llm):
    import openai

    return openai.OpenAI(base_url=f"{llm.url}/v1", api_key="test-key", max_retries=0)


def _anthropic_client(llm):
    import anthropic

    return anthropic.Anthropic(base_url=llm.url, api_key="test-key", max_retries=0)


def _chat(client) -> None:
    client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
    )


def _events_for(backend, provider: str) -> list[dict]:
    """Delivered events for a provider, waiting out the record-after-respond
    race: the fake backend appends to .requests AFTER writing the response
    bytes, so the SDK call can return microseconds before the recording
    lands — assert-immediately would flake."""
    backend.wait_for(
        lambda b: any(ev.get("provider") == provider for ev in b.delivered_events()),
        timeout=2.0,
    )
    return [ev for ev in backend.delivered_events() if ev.get("provider") == provider]


def _openai_events(backend) -> list[dict]:
    return _events_for(backend, "openai")


def test_openai_nonstreaming_baseline(fake_backend, fake_llm):
    """Regression guard: the already-working path stays working.

    One call -> exactly one event with the fake server's 7/3 usage.
    """
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    _chat(_openai_client(fake_llm))
    auto.stop()

    events = _openai_events(fake_backend)
    assert len(events) == 1
    assert events[0]["tokens_input"] == 7
    assert events[0]["tokens_output"] == 3
    assert events[0]["model"] == "gpt-4o"


def test_double_instrument_does_not_stack_wrappers(fake_backend, fake_llm):
    """Calling auto_instrument twice must not wrap the wrapper.

    Red today: the second patch captures the first wrapper as its
    "original", chaining new->old->real; uninstrument() doesn't exist, so
    nothing can ever restore the pristine method.
    """
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    _chat(_openai_client(fake_llm))
    auto.stop()

    events = _openai_events(fake_backend)
    assert len(events) == 1, f"expected exactly 1 event, got {len(events)}"

    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    auto.uninstrument()
    assert _oai_chat.Completions.create is _pristine("oai_sync"), (
        "uninstrument() must restore the pristine method — wrapper stacking "
        "makes full restoration impossible"
    )


def test_stop_unpatches_everything(fake_backend):
    """auto.stop() must remove the patches, not leave them aimed at a dead
    tracker.

    Red today: stop() closes the tracker but the patched methods remain
    installed forever.
    """
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    auto.stop()

    assert _oai_chat.Completions.create is _pristine("oai_sync"), (
        "openai patch left installed after stop()"
    )
    assert _anth_messages.Messages.create is _pristine("anth_sync"), (
        "anthropic patch left installed after stop()"
    )


def test_openai_stream_preserves_sdk_contract(fake_backend, fake_llm):
    """A patched streaming call must honor the SDK's Stream object contract.

    User code legitimately does `with client.chat.completions.create(
    stream=True) as s:` — the SDK returns a Stream context manager.
    Red today: the wrapper returns a bare generator; `with` raises
    TypeError, and .close() doesn't exist.
    """
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    client = _openai_client(fake_llm)

    chunks = []
    with client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    ) as stream:
        for chunk in stream:
            chunks.append(chunk)

    auto.stop()
    assert chunks, "stream yielded nothing"
    events = _openai_events(fake_backend)
    assert len(events) == 1, "streaming usage was not captured"
    assert events[0]["tokens_input"] == 7
    assert events[0]["tokens_output"] == 3


def test_anthropic_messages_stream_is_instrumented(fake_backend, fake_llm):
    """client.messages.stream(...) — Anthropic's recommended streaming API —
    must be counted.

    Red today: it does not route through Messages.create, so the patch
    never sees it and no event is logged.
    """
    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    client = _anthropic_client(fake_llm)

    text = []
    with client.messages.stream(
        model="claude-sonnet-test",
        max_tokens=64,
        messages=[{"role": "user", "content": "hi"}],
    ) as stream:
        for piece in stream.text_stream:
            text.append(piece)

    auto.stop()
    assert text, "text_stream yielded nothing"
    events = _events_for(fake_backend, "anthropic")
    assert len(events) == 1, "messages.stream() usage was not captured"
    assert events[0]["tokens_input"] == 7
    assert events[0]["tokens_output"] == 3


def test_anthropic_stream_enter_failure_is_logged(fake_backend):
    """A failure STARTING a messages.stream() session must be logged.

    Python never calls __exit__ when __enter__ raises, so the enter path
    needs its own failure logging — a connection/auth error on session
    start is exactly the failure users most need visibility into.
    (Regression test for the review finding: this path logged nothing.)
    """
    import anthropic

    auto.auto_instrument(endpoint=fake_backend.url, api_key="k")
    # 127.0.0.1:9 (discard port) refuses connections; max_retries=0 keeps
    # the failure immediate-ish (~2s connection refusal on Windows).
    client = anthropic.Anthropic(
        base_url="http://127.0.0.1:9", api_key="test-key", max_retries=0
    )

    with pytest.raises(anthropic.APIConnectionError):
        with client.messages.stream(
            model="claude-sonnet-test",
            max_tokens=64,
            messages=[{"role": "user", "content": "hi"}],
        ):
            pass  # never reached — __enter__ raises

    auto.stop()
    events = _events_for(fake_backend, "anthropic")
    assert len(events) == 1, "enter-failure was not logged"
    assert events[0]["status"] == "failure"


def test_stream_usage_injection_can_be_disabled(fake_backend, fake_llm):
    """auto_instrument(inject_stream_usage=False) must not mutate the
    user's request.

    Injecting stream_options breaks Azure OpenAI api-versions that reject
    the parameter — observability must never alter application behavior
    without an escape hatch. Red today: the kwarg doesn't exist.
    """
    auto.auto_instrument(
        endpoint=fake_backend.url, api_key="k", inject_stream_usage=False
    )
    client = _openai_client(fake_llm)

    with client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    ) as stream:
        for _ in stream:
            pass

    auto.stop()
    sent = fake_llm.bodies("/chat/completions")
    assert sent, "no request reached the fake LLM server"
    assert "stream_options" not in sent[-1], (
        "stream_options was injected despite inject_stream_usage=False"
    )
