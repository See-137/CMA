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


def _int(value: Any) -> int:
    # Only real ints count (bool is excluded; mocks and None become 0).
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0


def _first_int(obj: Any, *names: str) -> int | None:
    """First of ``names`` present as a real int (0 included), else None."""
    for name in names:
        value = _get(obj, name)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(value, 0)
    return None


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
    out["tokens_output"] = _first_int(
        usage, "completion_tokens", "output_tokens"
    ) or 0

    # Anthropic shape: a cache field is present as a real int. (Older
    # responses carry None there; their input_tokens is then all input.)
    if _first_int(usage, "cache_read_input_tokens", "cache_creation_input_tokens") is not None:
        read = _int(_get(usage, "cache_read_input_tokens"))
        write = _int(_get(usage, "cache_creation_input_tokens"))
        out["tokens_input"] = _int(_get(usage, "input_tokens")) + read + write
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


def anthropic_delta_input(usage: Any) -> dict[str, int] | None:
    """Input-side fields from a ``message_delta`` usage, or None when the
    delta carries no cache counts (then ``message_start``'s values stand:
    a delta with input_tokens but no cache fields would drop the cache)."""
    if _first_int(usage, "cache_read_input_tokens", "cache_creation_input_tokens") is None:
        return None
    return usage_fields(usage)
