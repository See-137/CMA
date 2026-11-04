import secrets

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import _verify_password, get_db, safe_compare
from app.config import settings
from app.models.models import SetupConfig
from app.schemas.schemas import (
    ReconnectRequest,
    SetupRequest,
    SetupResponse,
    SetupStatusResponse,
)
from app.services import credentials
from app.services.rate_limit import limit_account_failures, rate_limit
from app.timeutils import deployment_tz

router = APIRouter(prefix="/setup", tags=["setup"])

# Shares the "login" bucket: reconnect hands out the API key on a correct
# password, so it must be guessed no faster than /auth/login.
_login_limit = rate_limit("login", lambda: settings.RATE_LIMIT_LOGIN_PER_MINUTE)


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_db_knows_zone(db: Session, zone: str) -> None:
    """Postgres buckets dashboards in SQL with its own tz database, which may
    lack names Python knows (legacy links such as "Israel"). Reject those at
    setup rather than failing every dashboard query later."""
    if db.get_bind().dialect.name != "postgresql":
        return
    try:
        with db.begin_nested():
            db.execute(text("SELECT timezone(:tz, now())"), {"tz": zone})
    except DBAPIError:
        raise HTTPException(
            status_code=422,
            detail=f"The database does not know time zone {zone!r}; "
            "choose its canonical name (e.g. 'Asia/Jerusalem' rather than 'Israel').",
        ) from None


@router.post("", response_model=SetupResponse)
def run_setup(body: SetupRequest, db: Session = Depends(get_db)):
    existing = db.query(SetupConfig).first()
    if existing and existing.is_setup_complete:
        raise HTTPException(status_code=400, detail="Setup already completed")
    check_db_knows_zone(db, body.timezone)

    # The column is required but unusable on a new install: legacy_key_enabled
    # stays False, so agents use the ingest key below and people log in.
    unused_shared_key = secrets.token_urlsafe(32)

    if existing:
        existing.admin_email = body.admin_email
        existing.admin_password_hash = _hash_password(body.admin_password)
        existing.deployment_name = body.deployment_name
        existing.timezone = body.timezone
        existing.currency = body.currency
        existing.api_key = unused_shared_key
        existing.is_setup_complete = True
    else:
        config = SetupConfig(
            admin_email=body.admin_email,
            admin_password_hash=_hash_password(body.admin_password),
            deployment_name=body.deployment_name,
            timezone=body.timezone,
            currency=body.currency,
            api_key=unused_shared_key,
            is_setup_complete=True,
        )
        db.add(config)

    _, ingest_key = credentials.create_ingest_key(db, "default")
    try:
        db.commit()
    except IntegrityError as exc:
        # Two first-run requests raced: the single-row key let one through.
        db.rollback()
        raise HTTPException(status_code=400, detail="Setup already completed") from exc
    token, _ = credentials.create_session(db)
    return SetupResponse(
        message="Setup completed successfully",
        api_key=ingest_key,
        session_token=token,
        is_complete=True,
    )


@router.post(
    "/reconnect", response_model=SetupResponse, dependencies=[Depends(_login_limit)]
)
def reconnect(body: ReconnectRequest, db: Session = Depends(get_db)):
    """Log in from the setup screen. Keys are not shown again: Settings makes new ones."""
    config = db.query(SetupConfig).first()
    if not config or not config.is_setup_complete:
        raise HTTPException(status_code=400, detail="Setup not completed yet")

    email_ok = safe_compare(config.admin_email, body.admin_email)
    password_ok = _verify_password(body.admin_password, config.admin_password_hash)
    if not (email_ok and password_ok):
        limit_account_failures("login", settings.RATE_LIMIT_LOGIN_PER_MINUTE * 5)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token, _ = credentials.create_session(db)
    return SetupResponse(
        message="Reconnected successfully",
        session_token=token,
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
        effective_timezone=deployment_tz(db).key,
        currency=config.currency,
    )
