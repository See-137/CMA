"""Ingest keys and the state of the original shared key (decision D4)."""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import _bearer, get_db, require_admin, safe_compare
from app.models.models import IngestKey, SetupConfig
from app.schemas.schemas import (
    CredentialStatus,
    IngestKeyCreate,
    IngestKeyCreated,
    IngestKeyOut,
)
from app.services import credentials
from app.timeutils import utcnow

router = APIRouter(prefix="/keys", tags=["keys"])


def _key_or_404(key_id: int, db: Session) -> IngestKey:
    key = db.query(IngestKey).filter(IngestKey.id == key_id).first()
    if key is None:
        raise HTTPException(status_code=404, detail="Key not found")
    return key


def _created(key: IngestKey, token: str) -> IngestKeyCreated:
    return IngestKeyCreated(**IngestKeyOut.model_validate(key).model_dump(), key=token)


@router.get("", response_model=list[IngestKeyOut])
def list_keys(db: Session = Depends(get_db)):
    return db.query(IngestKey).order_by(IngestKey.id).all()


@router.post("", response_model=IngestKeyCreated, status_code=201)
def create_key(body: IngestKeyCreate, db: Session = Depends(get_db)):
    key, token = credentials.create_ingest_key(db, body.name)
    db.commit()
    db.refresh(key)
    return _created(key, token)


@router.post("/{key_id}/revoke", response_model=IngestKeyOut)
def revoke_key(key_id: int, db: Session = Depends(get_db)):
    key = _key_or_404(key_id, db)
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        db.commit()
        db.refresh(key)
    return key


@router.post("/{key_id}/rotate", response_model=IngestKeyCreated, status_code=201)
def rotate_key(key_id: int, db: Session = Depends(get_db)):
    """A new key under the same name; the old one stops working at once."""
    old = _key_or_404(key_id, db)
    if old.revoked_at is None:
        old.revoked_at = utcnow()
    key, token = credentials.create_ingest_key(db, old.name)
    db.commit()
    db.refresh(key)
    return _created(key, token)


@router.get("/status", response_model=CredentialStatus)
def credential_status(
    authorization: str | None = Header(None), db: Session = Depends(get_db)
):
    config = db.query(SetupConfig).first()
    shared = bool(config.legacy_key_enabled) and safe_compare(
        config.api_key, _bearer(authorization)
    )
    return CredentialStatus(
        role="shared_key" if shared else "session",
        shared_key_enabled=bool(config.legacy_key_enabled),
        shared_key_full_access_until=config.legacy_full_until
        if config.legacy_key_enabled
        else None,
    )


@router.post(
    "/shared/retire",
    response_model=CredentialStatus,
    dependencies=[Depends(require_admin)],
)
def retire_shared_key(
    authorization: str | None = Header(None), db: Session = Depends(get_db)
):
    """Turn the original shared key off for good, ingest included. Agents still
    using it stop reporting, so move them to ingest keys first."""
    config = db.query(SetupConfig).first()
    config.legacy_key_enabled = False
    db.commit()
    return CredentialStatus(role="session", shared_key_enabled=False)
