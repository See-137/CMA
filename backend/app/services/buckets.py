"""Time buckets in the deployment time zone (decision D2).

Dashboard series and Scrooge timelines group events by *local* hour or day.
Storage is naive UTC, so the grouping expression converts in SQL:

* PostgreSQL: ``date_trunc(unit, timezone(tz, timezone('UTC', ts)))`` — exact,
  DST-aware, done by the database.
* SQLite (no time-zone support): a Python function registered on every SQLite
  connection (``cma_local_bucket``) using ``zoneinfo`` — same result.

Both yield the bucket's local wall-clock start; :func:`bucket_start_utc`
turns it back into a naive-UTC instant for the API (emitted with ``Z``).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.timeutils import from_local, parse_zone, to_local

_UNITS = ("hour", "day")
_FMT = "%Y-%m-%d %H:%M"


@lru_cache(maxsize=64)
def _zone(name: str) -> ZoneInfo | None:
    return parse_zone(name)


def _sqlite_local_bucket(value: str | None, unit: str, tz_name: str) -> str | None:
    """SQLite UDF: the bucket key for a naive-UTC timestamp string.

    hour -> the UTC instant at which the containing local hour began (exact
            even when a local hour repeats at a DST fall-back);
    day  -> the local calendar date at midnight (a date is never ambiguous).
    Both as 'YYYY-MM-DD HH:MM'.
    """
    if value is None:
        return None
    # Every zone offset and DST transition falls on a whole minute, so the
    # bucket depends only on the minute: cache on it (large group-bys).
    return _bucket_for_minute(str(value)[:16], unit, tz_name)


@lru_cache(maxsize=65536)
def _bucket_for_minute(minute: str, unit: str, tz_name: str) -> str:
    tz = _zone(tz_name) or ZoneInfo("UTC")
    local = to_local(datetime.fromisoformat(minute), tz)
    if unit == "hour":
        start = local.replace(minute=0, second=0, microsecond=0)  # keeps fold
        return from_local(start, tz).strftime(_FMT)
    return local.replace(
        tzinfo=None, hour=0, minute=0, second=0, microsecond=0
    ).strftime(_FMT)


def register_sqlite_functions(dbapi_connection) -> None:
    """Called for every new SQLite connection (see app.database)."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.create_function(
            "cma_local_bucket", 3, _sqlite_local_bucket, deterministic=True
        )


def local_bucket_expr(column, unit: str, tz: ZoneInfo, db: Session):
    """SQL expression for the bucket key of ``column`` (see _sqlite_local_bucket):
    hour keys are UTC instants, day keys are local calendar dates."""
    if unit not in _UNITS:
        raise ValueError(f"unknown bucket unit {unit!r}")
    if db.get_bind().dialect.name == "sqlite":
        return func.cma_local_bucket(column, unit, tz.key)
    local = func.timezone(tz.key, func.timezone("UTC", column))
    if unit == "hour":
        # ts minus the time elapsed since the local hour began: the hour's
        # start as a UTC instant, so the two 01:00 hours of a fall-back day
        # stay separate buckets.
        return column - (local - func.date_trunc("hour", local))
    return func.date_trunc("day", local)


def bucket_start_utc(value, unit: str, tz: ZoneInfo) -> datetime:
    """Bucket key (SQLite string or Postgres naive datetime) -> naive-UTC
    bucket start. Day keys are local midnights (first occurrence)."""
    if isinstance(value, str):
        value = datetime.strptime(value, _FMT)  # noqa: DTZ007 - naive by design
    if unit == "hour":
        return value
    return from_local(value.replace(fold=0), tz)
