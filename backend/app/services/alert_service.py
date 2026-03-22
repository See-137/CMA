import json
import logging
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.models.models import Alert, AlertChannel

logger = logging.getLogger(__name__)


def dispatch_alert(alert: Alert, db: Session) -> None:
    """Send alert to all active channels. Failures are logged, not raised."""
    channels = db.query(AlertChannel).filter(AlertChannel.is_active.is_(True)).all()

    for channel in channels:
        try:
            config = json.loads(channel.config_json)
        except (json.JSONDecodeError, TypeError):
            logger.error("Invalid config_json for channel %s", channel.id)
            continue

        try:
            if channel.channel_type == "webhook":
                _send_webhook(alert, config)
            elif channel.channel_type == "slack":
                _send_slack(alert, config)
            elif channel.channel_type == "in_app":
                pass  # already persisted in DB
            else:
                logger.warning(
                    "Unsupported channel type %s for channel %s",
                    channel.channel_type,
                    channel.id,
                )
        except Exception:
            logger.exception(
                "Failed to dispatch alert %s to channel %s", alert.id, channel.id
            )


def _send_webhook(alert: Alert, config: dict) -> None:
    url = config.get("url")
    if not url:
        logger.error("Webhook channel missing 'url' in config")
        return

    payload = {
        "type": alert.alert_type,
        "severity": alert.severity,
        "message": alert.message,
        "timestamp": datetime.utcnow().isoformat(),
    }
    with httpx.Client(timeout=10) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
    logger.info("Webhook delivered to %s (status %s)", url, resp.status_code)


def _send_slack(alert: Alert, config: dict) -> None:
    webhook_url = config.get("webhook_url")
    if not webhook_url:
        logger.error("Slack channel missing 'webhook_url' in config")
        return

    severity_emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(
        alert.severity, "ℹ️"
    )
    text = f"{severity_emoji} *[{alert.severity.upper()}]* {alert.message}"

    with httpx.Client(timeout=10) as client:
        resp = client.post(webhook_url, json={"text": text})
        resp.raise_for_status()
    logger.info("Slack message delivered (status %s)", resp.status_code)
