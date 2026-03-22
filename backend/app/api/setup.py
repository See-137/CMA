import secrets

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.models import SetupConfig
from app.schemas.schemas import SetupRequest, SetupResponse, SetupStatusResponse

router = APIRouter(prefix="/setup", tags=["setup"])


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
