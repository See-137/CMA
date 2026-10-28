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
    """SQLite UDF: local wall-clock start of the hour/day containing a naive
    UTC timestamp string, as 'YYYY-MM-DD HH:MM'."""
    if value is None:
        return None
    tz = _zone(tz_name) or ZoneInfo("UTC")
    local = to_local(datetime.fromisoformat(str(value)), tz).replace(tzinfo=None)
    if unit == "hour":
        local = local.replace(minute=0, second=0, microsecond=0)
    else:
        local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local.strftime(_FMT)


def register_sqlite_functions(dbapi_connection) -> None:
    """Called for every new SQLite connection (see app.database)."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.create_function(
            "cma_local_bucket", 3, _sqlite_local_bucket, deterministic=True
        )


def local_bucket_expr(column, unit: str, tz: ZoneInfo, db: Session):
    """SQL expression: local wall-clock start of the bucket holding ``column``."""
    if unit not in _UNITS:
        raise ValueError(f"unknown bucket unit {unit!r}")
    if db.get_bind().dialect.name == "sqlite":
        return func.cma_local_bucket(column, unit, tz.key)
    return func.date_trunc(unit, func.timezone(tz.key, func.timezone("UTC", column)))


def bucket_start_utc(value, tz: ZoneInfo) -> datetime:
    """Local bucket start (SQLite string or Postgres naive datetime) -> naive
    UTC instant."""
    if isinstance(value, str):
        value = datetime.strptime(value, _FMT).replace(tzinfo=tz)
    return from_local(value, tz)
