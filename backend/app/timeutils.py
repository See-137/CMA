"""Time helpers.

`datetime.utcnow()` is deprecated; this returns the same value (naive UTC) so it
stays compatible with the timezone-naive DateTime columns used across the schema.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (no tzinfo)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
