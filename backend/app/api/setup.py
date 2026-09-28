import hmac
import secrets

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import _verify_password, get_db
from app.config import settings
from app.models.models import SetupConfig
from app.schemas.schemas import SetupRequest, SetupResponse, SetupStatusResponse
from app.services.rate_limit import rate_limit

router = APIRouter(prefix="/setup", tags=["setup"])

# Shares the "login" bucket: reconnect hands out the API key on a correct
# password, so it must be guessed no faster than /auth/login.
_login_limit = rate_limit("login", lambda: settings.RATE_LIMIT_LOGIN_PER_MINUTE)


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


@router.post("", response_model=SetupResponse)
def run_setup(body: SetupRequest, db: Session = Depends(get_db)):
    existing = db.query(SetupConfig).first()
    if existing and existing.is_setup_complete:
        raise HTTPException(status_code=400, detail="Setup already completed")

    api_key = secrets.token_urlsafe(32)

    if existing:
        existing.admin_email = body.admin_email
        existing.admin_password_hash = _hash_password(body.admin_password)
        existing.deployment_name = body.deployment_name
        existing.timezone = body.timezone
        existing.currency = body.currency
        existing.api_key = api_key
        existing.is_setup_complete = True
    else:
        config = SetupConfig(
            admin_email=body.admin_email,
            admin_password_hash=_hash_password(body.admin_password),
            deployment_name=body.deployment_name,
            timezone=body.timezone,
            currency=body.currency,
            api_key=api_key,
            is_setup_complete=True,
        )
        db.add(config)

    db.commit()
    return SetupResponse(
        message="Setup completed successfully",
        api_key=api_key,
        is_complete=True,
    )


@router.post(
    "/reconnect", response_model=SetupResponse, dependencies=[Depends(_login_limit)]
)
def reconnect(body: SetupRequest, db: Session = Depends(get_db)):
    """Return existing API key after verifying admin credentials."""
    config = db.query(SetupConfig).first()
    if not config or not config.is_setup_complete:
        raise HTTPException(status_code=400, detail="Setup not completed yet")

    email_ok = hmac.compare_digest(config.admin_email, body.admin_email)
    password_ok = _verify_password(body.admin_password, config.admin_password_hash)
    if not (email_ok and password_ok):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return SetupResponse(
        message="Reconnected successfully",
        api_key=config.api_key,
        is_complete=True,
    )


@router.get("/status", response_model=SetupStatusResponse)
def get_setup_status(db: Session = Depends(get_db)):
    config = db.query(SetupConfig).first()
    if not config:
        return SetupStatusResponse(is_complete=False)
    return SetupStatusResponse(
        is_complete=config.is_setup_complete,
        deployment_name=config.deployment_name,
        timezone=config.timezone,
        currency=config.currency,
    )
