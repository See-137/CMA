import hashlib
import hmac
import logging
import time

import bcrypt
from fastapi import Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import SetupConfig
from app.services import credentials
from app.services.rate_limit import limit_failures

logger = logging.getLogger(__name__)

# bcrypt only defines behaviour up to 72 bytes; bcrypt>=5 raises beyond that.
BCRYPT_MAX_BYTES = 72


def _bytes(text: str) -> bytes:
    """Never raises: JSON can carry lone surrogates, which plain encode() refuses."""
    return text.encode("utf-8", "surrogatepass")


def safe_compare(a: str, b: str) -> bool:
    """Constant-time string comparison that accepts any characters.

    ``hmac.compare_digest`` raises TypeError on non-ASCII str, which turned a
    malformed credential into a 500 (SEC-08).
    """
    return hmac.compare_digest(_bytes(a), _bytes(b))


def _verify_password(plain: str, hashed: str) -> bool:
    """Constant-time password check supporting bcrypt and legacy SHA-256."""
    raw = _bytes(plain)
    if hashed.startswith(("$2b$", "$2a$")):
        # bcrypt 4 silently hashed the first 72 bytes of a longer password and
        # bcrypt 5 raises on one: truncating keeps those admins able to log in.
        return bcrypt.checkpw(raw[:BCRYPT_MAX_BYTES], hashed.encode())
    return hmac.compare_digest(hashlib.sha256(raw).hexdigest(), hashed)


_legacy_warned_at = 0.0


def _bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization format. Use: Bearer <api_key>",
        )
    return authorization[7:]


def _setup_config(db: Session) -> SetupConfig:
    config = db.query(SetupConfig).first()
    if not config or not config.is_setup_complete:
        raise HTTPException(status_code=503, detail="Setup not completed")
    return config


def _is_legacy_key(config: SetupConfig, token: str) -> bool:
    return bool(config.legacy_key_enabled) and safe_compare(config.api_key, token)


def require_ingest(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> None:
    """Sending events and checking budgets: any valid credential."""
    token = _bearer(authorization)
    config = _setup_config(db)
    if not (
        _is_legacy_key(config, token)
        or credentials.ingest_key_is_valid(db, token)
        or credentials.session_is_valid(db, token)
    ):
        raise HTTPException(status_code=401, detail="Invalid API key")


def require_management(
    response: Response,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> None:
    """Everything else: an admin session, or the original shared key during its
    deprecation window. An ingest key is refused, so a key leaked from an agent
    cannot edit prices or read the ledger (SEC-01)."""
    token = _bearer(authorization)
    config = _setup_config(db)
    if credentials.session_is_valid(db, token):
        return
    if _is_legacy_key(config, token):
        if credentials.legacy_key_has_full_access(config):
            _warn_legacy(response, config)
            return
        raise HTTPException(
            status_code=403,
            detail="This key can only send events now. Log in for management access.",
        )
    if credentials.ingest_key_is_valid(db, token):
        raise HTTPException(
            status_code=403,
            detail="Ingest keys can only send events and check budgets.",
        )
    raise HTTPException(status_code=401, detail="Invalid API key")


def _warn_legacy(response: Response, config: SetupConfig) -> None:
    global _legacy_warned_at
    until = config.legacy_full_until.date().isoformat()
    response.headers["X-CMA-Deprecated"] = f"shared key: management access ends {until}"
    now = time.monotonic()
    if now - _legacy_warned_at > 3600:
        _legacy_warned_at = now
        logger.warning(
            "Management request with the shared API key: this ends on %s. "
            "Log in instead, and give agents ingest keys.",
            until,
        )


def require_metrics_access(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> None:
    """Auth for /metrics: a metrics-scoped token, or management access as fallback.

    Prometheus scrape configs typically live in repo-managed YAML — putting
    the master API key there grants ingest + full read to anyone who can
    read the config. CMA_METRICS_TOKEN lets the scraper hold a credential
    that can ONLY read metrics. Unset, the API key still works.
    """
    from app.config import settings

    token = settings.METRICS_TOKEN
    if (
        token
        and authorization
        and authorization.startswith("Bearer ")
        and safe_compare(authorization[7:], token)
    ):
        return

    require_management(Response(), authorization=authorization, db=db)


def require_admin(
    request: Request,
    x_admin_password: str | None = Header(None),
    db: Session = Depends(get_db),
) -> None:
    """Gate destructive/sensitive operations behind the admin password.

    Distinct from the API key, which every ingestion client carries — a leaked
    ingest token must not be able to wipe or exfiltrate data.
    """
    config = db.query(SetupConfig).first()
    if not config or not config.is_setup_complete:
        raise HTTPException(status_code=503, detail="Setup not completed")

    if not x_admin_password or not _verify_password(
        x_admin_password, config.admin_password_hash
    ):
        from app.config import settings

        limit_failures("admin", request, settings.RATE_LIMIT_LOGIN_PER_MINUTE)
        raise HTTPException(
            status_code=403, detail="Admin password required for this operation"
        )


# Re-export for convenience — all route modules import from here
__all__ = [
    "get_db",
    "require_admin",
    "require_ingest",
    "require_management",
    "require_metrics_access",
]
