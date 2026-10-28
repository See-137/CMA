"""Normalise provider usage objects into CMA's token fields.

CMA's ``tokens_input`` counts ALL input tokens; the cache fields say how many
of them were read from or written to a prompt cache (ACC-08/09):

* Anthropic reports ``input_tokens`` WITHOUT its cache tokens, plus
  ``cache_read_input_tokens`` and ``cache_creation_input_tokens`` (split by
  TTL in ``cache_creation.ephemeral_5m_input_tokens`` /
  ``ephemeral_1h_input_tokens``). Total input = all three.
* OpenAI reports ``prompt_tokens`` (chat) or ``input_tokens`` (Responses)
  INCLUDING cached tokens, with the cached part in
  ``prompt_tokens_details.cached_tokens`` / ``input_tokens_details.cached_tokens``.

Works on SDK objects and plain dicts. Never raises; unknown shapes give 0s.
"""

from __future__ import annotations

from typing import Any


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _num(value: Any) -> int | None:
    """A token count as a non-negative int, or None when ``value`` is not a
    count. Accepts ints, integral floats, numpy-style integers and digit
    strings (what the pre-cache SDK passed through); rejects bool, None and
    mocks."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, str):
        value = value.strip()
        return max(int(value), 0) if value.isdigit() else None
    try:
        as_int = int(value)
        if as_int == value:  # float/numpy: integral values only
            return max(as_int, 0)
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _int(value: Any) -> int:
    return _num(value) or 0


def _first_int(obj: Any, *names: str) -> int | None:
    """First non-zero count among ``names``, else the first present count
    (0 included), else None — e.g. ``prompt_tokens=0, input_tokens=12`` is 12,
    as before cache support."""
    found = [n for n in (_num(_get(obj, name)) for name in names) if n is not None]
    for n in found:
        if n:
            return n
    return found[0] if found else None


def _has_anthropic_cache(usage: Any) -> bool:
    return (
        _num(_get(usage, "cache_read_input_tokens")) is not None
        or _num(_get(usage, "cache_creation_input_tokens")) is not None
    )


def usage_fields(usage: Any) -> dict[str, int]:
    """CMA token fields for one provider usage object (either shape)."""
    out = {
        "tokens_input": 0,
        "tokens_output": 0,
        "tokens_cache_read": 0,
        "tokens_cache_write": 0,
        "tokens_cache_write_1h": 0,
    }
    if usage is None:
        return out
    out["tokens_output"] = _first_int(usage, "completion_tokens", "output_tokens") or 0

    # Anthropic shape: a cache field is present as a count. (Older responses
    # carry None there; their input_tokens is then all input.)
    if _has_anthropic_cache(usage):
        read = _int(_get(usage, "cache_read_input_tokens"))
        write = _int(_get(usage, "cache_creation_input_tokens"))
        base = _num(_get(usage, "input_tokens"))
        prompt = _num(_get(usage, "prompt_tokens"))
        if base is None and prompt is not None:
            # OpenAI-compatible proxies (e.g. LiteLLM) report prompt_tokens,
            # which already INCLUDES the cache counts.
            total = max(prompt, read + write)
        else:
            total = (base or 0) + read + write
        out["tokens_input"] = total
        out["tokens_cache_read"] = read
        out["tokens_cache_write"] = write
        out["tokens_cache_write_1h"] = min(
            _int(_get(_get(usage, "cache_creation"), "ephemeral_1h_input_tokens")),
            write,
        )
        return out

    tokens_in = _first_int(usage, "prompt_tokens", "input_tokens") or 0
    details = _get(usage, "prompt_tokens_details")
    if details is None:
        details = _get(usage, "input_tokens_details")
    cached = min(_int(_get(details, "cached_tokens")), tokens_in)
    out["tokens_input"] = tokens_in
    out["tokens_cache_read"] = cached
    return out


def cache_kwargs(fields: dict[str, int]) -> dict[str, int]:
    """The cache fields of ``usage_fields`` output, for ``log_event``."""
    return {
        k: fields[k]
        for k in ("tokens_cache_read", "tokens_cache_write", "tokens_cache_write_1h")
    }


def merge_anthropic_delta(fields: dict[str, int], usage: Any) -> dict[str, int]:
    """Input-side fields after a streaming ``message_delta``.

    Delta counts are cumulative, but any of them may be None, and a delta
    carries no ``cache_creation`` TTL split. So each count present in the
    delta replaces ``message_start``'s; an absent one keeps it; the 1-hour
    write count is kept (capped at the new write total) unless the delta
    states it."""
    old_read = fields["tokens_cache_read"]
    old_write = fields["tokens_cache_write"]
    old_base = max(fields["tokens_input"] - old_read - old_write, 0)
    base = _num(_get(usage, "input_tokens"))
    read = _num(_get(usage, "cache_read_input_tokens"))
    write = _num(_get(usage, "cache_creation_input_tokens"))
    if base is None and read is None and write is None:
        return fields
    read = old_read if read is None else read
    write = old_write if write is None else write
    base = old_base if base is None else base
    one_hour = _num(_get(_get(usage, "cache_creation"), "ephemeral_1h_input_tokens"))
    if one_hour is None:
        one_hour = fields["tokens_cache_write_1h"]
    return {
        **fields,
        "tokens_input": base + read + write,
        "tokens_cache_read": read,
        "tokens_cache_write": write,
        "tokens_cache_write_1h": min(one_hour, write),
    }
