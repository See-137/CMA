import bcrypt
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import (
    BCRYPT_MAX_BYTES,
    _bytes,
    _verify_password,
    get_db,
    safe_compare,
)
from app.config import settings
from app.models.models import SetupConfig
from app.schemas.schemas import LoginRequest, LoginResponse
from app.services import credentials
from app.services.rate_limit import limit_account_failures, rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])

_login_limit = rate_limit("login", lambda: settings.RATE_LIMIT_LOGIN_PER_MINUTE)


def _needs_rehash(hashed: str) -> bool:
    """Check if password hash needs upgrade to bcrypt."""
    return not hashed.startswith(("$2b$", "$2a$"))


@router.post(
    "/login", response_model=LoginResponse, dependencies=[Depends(_login_limit)]
)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    config = db.query(SetupConfig).first()
    if not config or not config.is_setup_complete:
        raise HTTPException(status_code=400, detail="Setup not completed")

    email_ok = safe_compare(config.admin_email, body.email)
    password_ok = _verify_password(body.password, config.admin_password_hash)
    if not (email_ok and password_ok):
        limit_account_failures("login", settings.RATE_LIMIT_LOGIN_PER_MINUTE * 5)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Transparently upgrade legacy SHA-256 hashes to bcrypt on successful login
    # (a legacy password over bcrypt's 72 bytes keeps its old hash)
    if (
        _needs_rehash(config.admin_password_hash)
        and len(_bytes(body.password)) <= BCRYPT_MAX_BYTES
    ):
        config.admin_password_hash = bcrypt.hashpw(
            _bytes(body.password), bcrypt.gensalt()
        ).decode()
        db.commit()

    token, expires = credentials.create_session(db)
    return LoginResponse(
        session_token=token, expires_at=expires, email=config.admin_email
    )


@router.post("/logout", status_code=204)
def logout(authorization: str | None = Header(None), db: Session = Depends(get_db)):
    """End this session. Harmless with any other credential."""
    if authorization and authorization.startswith("Bearer "):
        credentials.revoke_session(db, authorization[7:])
