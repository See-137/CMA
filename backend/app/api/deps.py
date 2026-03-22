from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import SetupConfig


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

    if config.api_key != api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return api_key


# Re-export for convenience — all route modules import from here
__all__ = ["get_db", "require_api_key"]
