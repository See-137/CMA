"""Time helpers.

`datetime.utcnow()` is deprecated; this returns the same value (naive UTC) so it
stays compatible with the timezone-naive DateTime columns used across the schema.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (no tzinfo)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: datetime | None) -> datetime | None:
    """Normalise a datetime to naive UTC for storage/comparison.

    The schema stores naive UTC everywhere. A timezone-aware value is converted
    to UTC first; storing it as-is would let the driver drop the offset (SQLite)
    or convert to the *server* session time zone (PostgreSQL), mis-dating the
    event by the offset. Naive inputs are taken to already be UTC.
    """
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
