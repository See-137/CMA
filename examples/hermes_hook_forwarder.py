#!/usr/bin/env python3
"""Hermes/Jarvis -> CMA event forwarder (post_api_request shell hook).

Reads a Hermes `post_api_request` hook payload from stdin and forwards it to a
CMA instance as a cost event. Designed to run on the hook path of a live
agent, so it must never block or fail loudly:

- stdlib only (no venv dependency — runs under system python3)
- bounded stdin read (SIGALRM) and a hard 3s network timeout; always exits 0
- on transient delivery failure, events spool to a local JSONL file (5 MB cap,
  flock-guarded) and are re-flushed opportunistically on the next successful
  invocation; permanently rejected events (4xx) are dropped, never re-spooled

Config lives in ~/.hermes/agent-hooks/cma_forward.env (chmod 600):
    CMA_ENDPOINT=http://127.0.0.1:8000
    CMA_API_KEY=<ingest api key>
    CMA_AGENT_NAME=jarvis          # optional, defaults to "jarvis"

Wire shape: Hermes pipes {"hook_event_name", "session_id", "cwd", "extra":
{...event kwargs...}} to stdin. `hermes hooks test` sends the kwargs flat
instead — _map_event handles both by merging `extra` over the top level.

Token accounting: CMA's cost model is flat input/output price. Anthropic bills
cache writes at 1.25x and cache reads at 0.1x of the input rate, so we fold
cache traffic into a billed-equivalent input token count (cost stays accurate;
raw counts are preserved in metadata for auditing).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

ENV_PATH = os.path.expanduser("~/.hermes/agent-hooks/cma_forward.env")
SPOOL_PATH = os.path.expanduser("~/.hermes/agent-hooks/cma_spool.jsonl")
SPOOL_MAX_BYTES = 5 * 1024 * 1024
FLUSH_BATCH = 50
TIMEOUT_S = 3.0
STDIN_TIMEOUT_S = 5

# Delivery outcomes for _post
_OK, _TRANSIENT, _PERMANENT = "ok", "transient", "permanent"

# CMA seeds providers with title-case names; Hermes reports lowercase.
PROVIDER_NAMES = {"anthropic": "Anthropic", "openai": "OpenAI", "google": "Google"}

# Hermes config aliases use dots in version numbers (claude-sonnet-4.6);
# the canonical Anthropic API id uses dashes (claude-sonnet-4-6).
_DOT_VERSION = re.compile(r"^(claude-[a-z0-9]+(?:-[a-z0-9]+)*-\d+)\.(\d+)$")


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@contextlib.contextmanager
def _spool_lock():
    """Exclusive advisory lock serializing spool read-modify-write across
    concurrently firing hooks. No-op on platforms without fcntl (dev only —
    production target is Linux)."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows dev machines
        yield
        return
    os.makedirs(os.path.dirname(SPOOL_PATH), exist_ok=True)
    with open(SPOOL_PATH + ".lock", "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


def _load_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def _read_stdin_bounded() -> str:
    """Read all of stdin with a hard wall-clock bound so a harness bug can
    never park us on a never-EOF pipe."""
    try:
        import signal

        def _timeout(_sig, _frm):  # pragma: no cover - exercised on Linux only
            raise TimeoutError

        if hasattr(signal, "SIGALRM"):
            signal.signal(signal.SIGALRM, _timeout)
            signal.alarm(STDIN_TIMEOUT_S)
    except (ImportError, ValueError, OSError):
        pass
    try:
        return sys.stdin.read()
    finally:
        try:
            import signal

            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)
        except (ImportError, ValueError, OSError):
            pass


def _normalize_model(model: str) -> str:
    return _DOT_VERSION.sub(r"\1-\2", model)


def _billed_input_tokens(usage: dict) -> int:
    base = max(0, _to_int(usage.get("input_tokens")))
    creation = max(0, _to_int(usage.get("cache_creation_input_tokens")))
    read = max(0, _to_int(usage.get("cache_read_input_tokens")))
    return round(base + 1.25 * creation + 0.1 * read)


def _map_event(payload, agent_name: str) -> dict | None:
    if not isinstance(payload, dict):
        return None
    # Wire shape nests event kwargs under "extra"; `hermes hooks test` sends
    # them flat. Merge extra over top-level so both work.
    extra = payload.get("extra")
    p = {**payload, **extra} if isinstance(extra, dict) else payload
    usage = p.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    model = p.get("response_model") or p.get("model") or ""
    provider = str(p.get("provider") or "").lower()
    if not model or not provider:
        return None
    return {
        "agent_name": agent_name,
        "model": _normalize_model(str(model)),
        "provider": PROVIDER_NAMES.get(provider, provider.capitalize()),
        "tokens_input": _billed_input_tokens(usage),
        "tokens_output": max(0, _to_int(usage.get("output_tokens"))),
        "duration_ms": max(0, int(_to_float(p.get("api_duration")) * 1000)),
        "status": "error" if p.get("is_error") else "success",
        "workflow": p.get("platform") or None,
        "trace_id": p.get("task_id") or None,
        "metadata": {
            "source": "hermes-post_api_request",
            "session_id": p.get("session_id"),
            "api_call_count": p.get("api_call_count"),
            "finish_reason": p.get("finish_reason"),
            "usage_raw": usage,
        },
    }


def _post(endpoint: str, api_key: str, events: list[dict]) -> str:
    """POST events; classify the outcome so callers can retry sanely.

    4xx = the server understood and rejected (retrying is pointless);
    everything else that fails = transient (network, 5xx, timeout).
    """
    try:
        body = json.dumps({"events": events}).encode("utf-8")
    except (TypeError, ValueError):
        return _PERMANENT
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/api/v1/events",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return _OK if 200 <= resp.status < 300 else _TRANSIENT
    except urllib.error.HTTPError as exc:
        return _PERMANENT if 400 <= exc.code < 500 else _TRANSIENT
    except (urllib.error.URLError, OSError, ValueError):
        return _TRANSIENT


def _spool_append(event: dict) -> None:
    try:
        with _spool_lock():
            if (
                os.path.exists(SPOOL_PATH)
                and os.path.getsize(SPOOL_PATH) > SPOOL_MAX_BYTES
            ):
                return  # cap reached — drop rather than grow unbounded
            with open(SPOOL_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
    except OSError:
        pass


def _spool_flush(endpoint: str, api_key: str) -> None:
    """Drain up to FLUSH_BATCH spooled events. Batch-post first; if the batch
    is rejected outright (4xx), fall back to per-item posts so one poison
    event can't jam the queue — rejected items are dropped, transient
    failures stay spooled."""
    try:
        with _spool_lock():
            if not os.path.exists(SPOOL_PATH):
                return
            with open(SPOOL_PATH, encoding="utf-8") as f:
                lines = f.readlines()
            if not lines:
                return
            batch, rest = lines[:FLUSH_BATCH], lines[FLUSH_BATCH:]
            events: list[dict] = []
            for line in batch:
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        events.append(parsed)
                except json.JSONDecodeError:
                    continue  # corrupt line — skip permanently

            keep: list[str] = []
            if events:
                outcome = _post(endpoint, api_key, events)
                if outcome == _TRANSIENT:
                    return  # endpoint unreachable — leave spool untouched
                if outcome == _PERMANENT:
                    for ev in events:
                        item_outcome = _post(endpoint, api_key, [ev])
                        if item_outcome == _TRANSIENT:
                            keep.append(json.dumps(ev) + "\n")
                        # _OK delivered; _PERMANENT dropped (poison event)

            with open(SPOOL_PATH, "w", encoding="utf-8") as f:
                f.writelines(keep + rest)
    except OSError:
        pass


def main() -> int:
    env = _load_env(ENV_PATH)
    endpoint = env.get("CMA_ENDPOINT", "")
    api_key = env.get("CMA_API_KEY", "")
    if not endpoint or not api_key:
        return 0  # unconfigured — stay silent, never block the agent

    try:
        payload = json.loads(_read_stdin_bounded())
    except (json.JSONDecodeError, ValueError, TimeoutError):
        return 0

    event = _map_event(payload, env.get("CMA_AGENT_NAME", "jarvis"))
    if event is None:
        return 0

    outcome = _post(endpoint, api_key, [event])
    if outcome == _OK:
        _spool_flush(endpoint, api_key)
    elif outcome == _TRANSIENT:
        _spool_append(event)
    # _PERMANENT: rejected by the API — drop, re-sending can never succeed
    return 0


if __name__ == "__main__":
    # The one hard invariant: this script must never crash or block the
    # calling agent. Belt-and-suspenders over the whole entry point.
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - deliberate catch-all safety net
        sys.exit(0)
