import hashlib
import hmac

import bcrypt
from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import SetupConfig


def _verify_password(plain: str, hashed: str) -> bool:
    """Constant-time password check supporting bcrypt and legacy SHA-256."""
    if hashed.startswith("$2b$") or hashed.startswith("$2a$"):
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    return hmac.compare_digest(hashlib.sha256(plain.encode()).hexdigest(), hashed)


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

    if not hmac.compare_digest(config.api_key, api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    return api_key


def require_admin(
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
        raise HTTPException(
            status_code=403, detail="Admin password required for this operation"
        )


# Re-export for convenience — all route modules import from here
__all__ = ["get_db", "require_api_key", "require_admin"]
