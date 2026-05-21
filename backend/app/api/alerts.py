import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.models import Alert, AlertChannel
from app.schemas.schemas import AlertChannelCreate, AlertChannelOut, AlertOut
from app.services.alert_service import UnsafeWebhookURL, validate_external_url
from app.timeutils import utcnow

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
    alert.resolved_at = utcnow()
    db.commit()
    db.refresh(alert)
    return AlertOut.model_validate(alert)


@router.get("/channels", response_model=list[AlertChannelOut])
def list_channels(db: Session = Depends(get_db)):
    channels = db.query(AlertChannel).all()
    return [AlertChannelOut.model_validate(c) for c in channels]


@router.post("/channels", response_model=AlertChannelOut, status_code=201)
def create_channel(body: AlertChannelCreate, db: Session = Depends(get_db)):
    try:
        config = json.loads(body.config)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(
            status_code=422, detail="config must be valid JSON"
        ) from exc
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="config must be a JSON object")

    url_field = {"webhook": "url", "slack": "webhook_url"}.get(body.channel_type)
    if url_field:
        url = config.get(url_field)
        if not url:
            raise HTTPException(
                status_code=422,
                detail=f"{body.channel_type} channel requires '{url_field}' in config",
            )
        try:
            validate_external_url(url)
        except UnsafeWebhookURL as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    channel = AlertChannel(
        name=body.name,
        channel_type=body.channel_type,
        config_json=body.config,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return AlertChannelOut.model_validate(channel)


@router.delete("/channels/{channel_id}", response_model=AlertChannelOut)
def delete_channel(channel_id: int, db: Session = Depends(get_db)):
    channel = db.query(AlertChannel).filter(AlertChannel.id == channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Alert channel not found")

    channel.is_active = False
    db.commit()
    db.refresh(channel)
    return AlertChannelOut.model_validate(channel)
