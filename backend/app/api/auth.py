import hmac

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import _verify_password, get_db
from app.config import settings
from app.models.models import SetupConfig
from app.schemas.schemas import LoginRequest, LoginResponse
from app.services.rate_limit import rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])

_login_limit = rate_limit("login", lambda: settings.RATE_LIMIT_LOGIN_PER_MINUTE)


def _needs_rehash(hashed: str) -> bool:
    """Check if password hash needs upgrade to bcrypt."""
    return not (hashed.startswith("$2b$") or hashed.startswith("$2a$"))


@router.post(
    "/login", response_model=LoginResponse, dependencies=[Depends(_login_limit)]
)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    config = db.query(SetupConfig).first()
    if not config or not config.is_setup_complete:
        raise HTTPException(status_code=400, detail="Setup not completed")

    email_ok = hmac.compare_digest(config.admin_email, body.email)
    password_ok = _verify_password(body.password, config.admin_password_hash)
    if not (email_ok and password_ok):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Transparently upgrade legacy SHA-256 hashes to bcrypt on successful login
    if _needs_rehash(config.admin_password_hash):
        config.admin_password_hash = bcrypt.hashpw(
            body.password.encode(), bcrypt.gensalt()
        ).decode()
        db.commit()

    return LoginResponse(api_key=config.api_key, email=config.admin_email)
