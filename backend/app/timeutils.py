"""Time helpers.

`datetime.utcnow()` is deprecated; this returns the same value (naive UTC) so it
stays compatible with the timezone-naive DateTime columns used across the schema.
"""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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


# -- Deployment time zone (decision D2, docs/PLAN.md) -------------------------
#
# Storage stays naive UTC. Only *boundaries* follow the deployment time zone:
# "today", "this week", a daily budget, a dashboard day bucket all start at the
# operator's local midnight. Every helper below takes and returns naive UTC.

_logger = logging.getLogger(__name__)
UTC_ZONE = ZoneInfo("UTC")
_warned_zones: set[str] = set()


def parse_zone(name: str | None) -> ZoneInfo | None:
    """ZoneInfo for an IANA name, or None when it is not a known zone."""
    if not name or not isinstance(name, str):
        return None
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return None


def deployment_tz(db) -> ZoneInfo:
    """The time zone chosen at setup (SetupConfig.timezone), UTC if unset or
    unknown. Read per call (one tiny query) so every worker sees a change at
    once; nothing is cached across requests."""
    from app.models.models import SetupConfig

    name = db.query(SetupConfig.timezone).limit(1).scalar()
    zone = parse_zone(name)
    if zone is None:
        if name and name not in _warned_zones:
            _warned_zones.add(name)
            _logger.warning("Unknown deployment time zone %r; using UTC", name)
        return UTC_ZONE
    return zone


def to_local(value: datetime, tz: ZoneInfo) -> datetime:
    """Naive UTC -> aware local time."""
    return value.replace(tzinfo=timezone.utc).astimezone(tz)


def from_local(local: datetime, tz: ZoneInfo) -> datetime:
    """Local wall-clock time (naive or aware in ``tz``) -> naive UTC.

    A repeated wall time resolves by its ``fold`` (0 = first occurrence,
    the offset in force before the transition); a skipped one with fold=0
    maps to the instant after the gap.
    """
    aware = local.replace(tzinfo=tz) if local.tzinfo is None else local
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


_UNITS = ("hour", "day", "week", "month", "quarter")


def local_floor(unit: str, tz: ZoneInfo, now: datetime | None = None) -> datetime:
    """Start of the current local hour/day/week (Monday)/month/quarter, as
    naive UTC."""
    if unit not in _UNITS:
        raise ValueError(f"unknown unit {unit!r}")
    local = to_local(now or utcnow(), tz).replace(tzinfo=None)
    if unit == "hour":
        # Keeps fold: the second 01:00 of a fall-back night is its own hour.
        start = local.replace(minute=0, second=0, microsecond=0)
    else:
        # Calendar boundaries are the FIRST occurrence of local midnight
        # (fold=0), or a repeated midnight (e.g. America/Havana) would move
        # the start of "today" during the repeated hour.
        start = local.replace(hour=0, minute=0, second=0, microsecond=0, fold=0)
        if unit == "week":
            start -= timedelta(days=start.weekday())
        elif unit == "month":
            start = start.replace(day=1)
        elif unit == "quarter":
            start = start.replace(month=((start.month - 1) // 3) * 3 + 1, day=1)
    return from_local(start, tz)


def shift_local(
    value: datetime, tz: ZoneInfo, *, days: int = 0, months: int = 0
) -> datetime:
    """Move a naive-UTC instant by whole local days/months (wall-clock
    arithmetic, so a local day across a DST change is 23 or 25 hours)."""
    local = to_local(value, tz).replace(tzinfo=None)
    if months:
        index = local.year * 12 + (local.month - 1) + months
        local = local.replace(year=index // 12, month=index % 12 + 1)
    local += timedelta(days=days)
    return from_local(local.replace(fold=0), tz)


def local_bucket(value: datetime, unit: str, tz: ZoneInfo) -> datetime:
    """Start (naive UTC) of the local hour/day containing a naive-UTC instant."""
    return local_floor(unit, tz, now=value)


def utc_iso(value: datetime) -> str:
    """Naive UTC -> ISO-8601 with 'Z' (minute precision for buckets)."""
    return value.isoformat(timespec="minutes") + "Z"
