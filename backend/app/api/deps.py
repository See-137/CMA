import hashlib
import hmac

import bcrypt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import SetupConfig
from app.services.rate_limit import limit_failures

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


def require_api_key(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> str:
    """Validate API key from Authorization header. Returns the API key."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization format. Use: Bearer <api_key>",
        )

    api_key = authorization[7:]  # Strip "Bearer "

    config = db.query(SetupConfig).first()
    if not config or not config.is_setup_complete:
        raise HTTPException(status_code=503, detail="Setup not completed")

    if not safe_compare(config.api_key, api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    return api_key


def require_metrics_access(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> None:
    """Auth for /metrics: a metrics-scoped token, or the API key as fallback.

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

    require_api_key(authorization=authorization, db=db)


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
__all__ = ["get_db", "require_admin", "require_api_key", "require_metrics_access"]
