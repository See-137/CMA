"""Ingest keys, admin sessions and the deprecation of the original shared key
(decision D4).

Tokens are random and long, so a plain SHA-256 is enough to store them: nothing
is gained by a slow hash, and every ingest request looks one up.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.models import AdminSession, IngestKey, SetupConfig
from app.timeutils import utcnow

INGEST_PREFIX = "cmai_"
SESSION_PREFIX = "cmas_"
SESSION_HOURS = 12
# last_used_at is a hint for the operator; a write per request is not worth it,
# and on SQLite it would queue behind ingestion's write lock.
_LAST_USED_RESOLUTION = timedelta(minutes=5)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


def create_ingest_key(db: Session, name: str) -> tuple[IngestKey, str]:
    """A new key and its value, which is never available again. Not committed."""
    token = INGEST_PREFIX + secrets.token_urlsafe(32)
    key = IngestKey(
        name=name,
        key_hash=_hash(token),
        prefix=token[: len(INGEST_PREFIX) + 6],
        created_at=utcnow(),
    )
    db.add(key)
    return key, token


def create_session(db: Session) -> tuple[str, datetime]:
    """A new admin session token and its expiry. Commits."""
    now = utcnow()
    db.query(AdminSession).filter(AdminSession.expires_at < now).delete()
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    expires = now + timedelta(hours=SESSION_HOURS)
    db.add(AdminSession(token_hash=_hash(token), created_at=now, expires_at=expires))
    db.commit()
    return token, expires


def revoke_session(db: Session, token: str) -> None:
    db.query(AdminSession).filter(AdminSession.token_hash == _hash(token)).update(
        {"revoked_at": utcnow()}
    )
    db.commit()


def session_is_valid(db: Session, token: str) -> bool:
    row = (
        db.query(AdminSession.id)
        .filter(
            AdminSession.token_hash == _hash(token),
            AdminSession.revoked_at.is_(None),
            AdminSession.expires_at > utcnow(),
        )
        .first()
    )
    return row is not None


def ingest_key_is_valid(db: Session, token: str) -> bool:
    key = (
        db.query(IngestKey)
        .filter(IngestKey.key_hash == _hash(token), IngestKey.revoked_at.is_(None))
        .first()
    )
    if key is None:
        return False
    now = utcnow()
    if key.last_used_at is None or now - key.last_used_at > _LAST_USED_RESOLUTION:
        try:
            key.last_used_at = now
            db.commit()
        except SQLAlchemyError:
            db.rollback()  # a busy database must not fail the request
    return True


def legacy_key_has_full_access(config: SetupConfig) -> bool:
    return bool(
        config.legacy_key_enabled
        and config.legacy_full_until is not None
        and utcnow() < config.legacy_full_until
    )
