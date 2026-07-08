"""Regression tests for the SDK auto-instrumentation import-guard (B3 hardening).

The B3 hardening wrapped each internal ``from <sdk>.resources... import ...``
inside ``patch_openai`` / ``patch_anthropic`` in a ``try/except ImportError``
that logs a loud WARNING and no-ops, instead of letting the ImportError bubble
out of the public patch function.

These tests lock that contract in:

* Happy path -- ``patch_openai`` / ``patch_anthropic`` rebind the expected
  ``.create`` surfaces against the installed openai (2.x) / anthropic (0.x)
  SDKs.
* Drift path -- when an internal SDK module path can no longer be imported,
  the public patch function does NOT raise, emits a WARNING, and leaves
  surfaces patched by other successful calls intact.

The instrumentation mutates global SDK class attributes, so an autouse fixture
snapshots the pristine methods at import time and restores them after every
test to keep the monkey-patching from leaking across the suite.
"""

import logging
import sys
from typing import Any, Callable
from unittest.mock import Mock

import pytest

from cost_monitor._anthropic import patch_anthropic
from cost_monitor._openai import patch_openai

# Import the SDK submodules that own the patch targets so we can snapshot the
# pristine (unpatched) methods before any test mutates them.
from anthropic.resources import messages as anthropic_messages
from openai.resources import completions as openai_legacy
from openai.resources.chat import completions as openai_chat

# Every (class, attr) that patch_* rebinds. Kept as a list of triples with the
# original captured at import time so restoration is order-independent (two
# distinct "Completions" classes share a __qualname__, so a dict keyed on name
# would collide).
_PATCH_TARGETS: list[tuple[type, str]] = [
    (openai_chat.Completions, "create"),
    (openai_chat.AsyncCompletions, "create"),
    (openai_legacy.Completions, "create"),
    (anthropic_messages.Messages, "create"),
    (anthropic_messages.AsyncMessages, "create"),
]
_PRISTINE: list[tuple[type, str, Callable[..., Any]]] = [
    (cls, attr, getattr(cls, attr)) for cls, attr in _PATCH_TARGETS
]


def _is_patched(func: Callable[..., Any]) -> bool:
    """True if ``func`` is one of the instrumentation's replacement closures.

    ``patch_*`` rebinds each ``.create`` to a locally-defined ``patched_create``
    / ``patched_acreate``; the pristine SDK method is named ``create``.
    """
    return getattr(func, "__name__", "") in {"patched_create", "patched_acreate"}


@pytest.fixture(autouse=True)
def _restore_sdk_surfaces() -> Any:
    """Restore every patch target to its pristine method after each test."""
    yield
    for cls, attr, original in _PRISTINE:
        setattr(cls, attr, original)


@pytest.fixture
def tracker() -> Mock:
    """Stand-in ``CostTracker``.

    ``patch_*`` only stashes the tracker inside closures and never calls it at
    patch time, so a Mock avoids constructing the real tracker (which would spin
    up a background flush thread and an httpx client).
    """
    return Mock()


# --------------------------------------------------------------------------- #
# Happy path                                                                   #
# --------------------------------------------------------------------------- #
def test_patch_openai_patches_expected_surfaces(tracker: Mock) -> None:
    """patch_openai rebinds sync + async chat and legacy completions."""
    patch_openai(tracker)

    assert _is_patched(openai_chat.Completions.create)
    assert _is_patched(openai_chat.AsyncCompletions.create)
    assert _is_patched(openai_legacy.Completions.create)


def test_patch_anthropic_patches_expected_surfaces(tracker: Mock) -> None:
    """patch_anthropic rebinds sync + async messages."""
    patch_anthropic(tracker)

    assert _is_patched(anthropic_messages.Messages.create)
    assert _is_patched(anthropic_messages.AsyncMessages.create)


# --------------------------------------------------------------------------- #
# Drift path (the B3 regression guard)                                         #
# --------------------------------------------------------------------------- #
def test_patch_openai_survives_chat_import_drift(
    tracker: Mock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broken internal import must no-op with a WARNING, not raise.

    Also verifies isolation: a surface patched by an earlier successful call
    (anthropic) and a sibling surface in the same call (legacy completions)
    stay intact when the chat-completions import path is what drifted.
    """
    # Anthropic is patched successfully first; it must survive the later drift.
    patch_anthropic(tracker)
    anthropic_patched = anthropic_messages.Messages.create
    assert _is_patched(anthropic_patched)

    # Simulate the SDK layout changing so the internal path can no longer be
    # imported. Setting the *parent* package to None in sys.modules makes
    # ``from openai.resources.chat import completions`` raise ImportError.
    # (Blocking the submodule alone does not: the ``completions`` attribute is
    # resolved from the already-loaded parent package before any submodule
    # import is attempted.)
    monkeypatch.setitem(sys.modules, "openai.resources.chat", None)

    with caplog.at_level(logging.WARNING, logger="cost_monitor.auto.openai"):
        # Must not raise despite the broken import.
        patch_openai(tracker)

    # 1. The drifted surface is left unpatched (both sync and async chat).
    assert not _is_patched(openai_chat.Completions.create)
    assert not _is_patched(openai_chat.AsyncCompletions.create)

    # 2. A loud WARNING naming the broken module path was emitted.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a WARNING-level log when the SDK layout drifts"
    assert any("openai.resources.chat" in r.getMessage() for r in warnings)

    # 3. Other already-patched surfaces are untouched:
    #    - cross-provider: the anthropic patch from before is the same object.
    assert anthropic_messages.Messages.create is anthropic_patched
    #    - intra-call sibling: legacy completions (a different import path) was
    #      still patched successfully within the same patch_openai call.
    assert _is_patched(openai_legacy.Completions.create)
