from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.models import Alert, AlertChannel
from app.schemas.schemas import AlertChannelCreate, AlertChannelOut, AlertOut

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
def list_alerts(db: Session = Depends(get_db)):
    alerts = db.query(Alert).order_by(Alert.created_at.desc()).all()
    return [AlertOut.model_validate(a) for a in alerts]


@router.put("/{alert_id}/resolve", response_model=AlertOut)
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert.is_resolved = True
    alert.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(alert)
    return AlertOut.model_validate(alert)


@router.get("/channels", response_model=list[AlertChannelOut])
def list_channels(db: Session = Depends(get_db)):
    channels = db.query(AlertChannel).all()
    return [AlertChannelOut.model_validate(c) for c in channels]


@router.post("/channels", response_model=AlertChannelOut, status_code=201)
def create_channel(body: AlertChannelCreate, db: Session = Depends(get_db)):
    channel = AlertChannel(
        name=body.name,
        channel_type=body.channel_type,
        config_json=body.config,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return AlertChannelOut.model_validate(channel)
