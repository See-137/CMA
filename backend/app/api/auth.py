import hashlib

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.models import SetupConfig
from app.schemas.schemas import LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def _verify_password(plain: str, hashed: str) -> bool:
    """Check password against hash. Supports both bcrypt and legacy SHA-256."""
    # Try bcrypt first
    if hashed.startswith("$2b$") or hashed.startswith("$2a$"):
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    # Legacy SHA-256 fallback
    return hashlib.sha256(plain.encode()).hexdigest() == hashed


def _needs_rehash(hashed: str) -> bool:
    """Check if password hash needs upgrade to bcrypt."""
    return not (hashed.startswith("$2b$") or hashed.startswith("$2a$"))


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    config = db.query(SetupConfig).first()
    if not config or not config.is_setup_complete:
        raise HTTPException(status_code=400, detail="Setup not completed")

    if config.admin_email != body.email or not _verify_password(
        body.password, config.admin_password_hash
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Transparently upgrade legacy SHA-256 hashes to bcrypt on successful login
    if _needs_rehash(config.admin_password_hash):
        config.admin_password_hash = bcrypt.hashpw(
            body.password.encode(), bcrypt.gensalt()
        ).decode()
        db.commit()

    return LoginResponse(api_key=config.api_key, email=config.admin_email)
