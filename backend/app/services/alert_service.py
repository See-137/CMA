import ipaddress
import json
import logging
import socket
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import Alert, AlertChannel
from app.timeutils import utcnow

logger = logging.getLogger(__name__)


class UnsafeWebhookURL(ValueError):
    """Raised when a webhook URL targets a disallowed (private/internal) host."""


def validate_external_url(url: str) -> str:
    """Reject webhook targets that point at internal infrastructure (SSRF).

    Blocks non-http(s) schemes and any host that resolves to a private,
    loopback, link-local, or reserved address — unless CMA_ALLOW_PRIVATE_WEBHOOKS
    is set (for pointing alerts at a localhost dev endpoint).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeWebhookURL(f"Unsupported URL scheme: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise UnsafeWebhookURL("URL has no host")

    if settings.ALLOW_PRIVATE_WEBHOOKS:
        return url

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeWebhookURL(f"Could not resolve host: {host}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeWebhookURL(
                f"URL resolves to a disallowed address ({ip}) — refusing to send"
            )
    return url


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
    url = validate_external_url(url)

    payload = {
        "type": alert.alert_type,
        "severity": alert.severity,
        "message": alert.message,
        "timestamp": utcnow().isoformat(),
    }
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
    logger.info("Webhook delivered to %s (status %s)", url, resp.status_code)


def _send_slack(alert: Alert, config: dict) -> None:
    webhook_url = config.get("webhook_url")
    if not webhook_url:
        logger.error("Slack channel missing 'webhook_url' in config")
        return
    webhook_url = validate_external_url(webhook_url)

    severity_emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(
        alert.severity, "ℹ️"
    )
    text = f"{severity_emoji} *[{alert.severity.upper()}]* {alert.message}"

    with httpx.Client(timeout=10, follow_redirects=False) as client:
        resp = client.post(webhook_url, json={"text": text})
        resp.raise_for_status()
    logger.info("Slack message delivered (status %s)", resp.status_code)
