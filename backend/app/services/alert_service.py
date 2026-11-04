import ipaddress
import json
import logging
import socket
from urllib.parse import urlparse, urlunparse

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import Alert, AlertChannel
from app.timeutils import utcnow

logger = logging.getLogger(__name__)


class UnsafeWebhookURL(ValueError):
    """Raised when a webhook URL targets a disallowed (private/internal) host."""


def _disallowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    # is_global excludes private, loopback, link-local, CGNAT (100.64.0.0/10)
    # and documentation ranges; multicast must be listed separately because
    # global multicast counts as global.
    return not ip.is_global or ip.is_multicast or ip.is_unspecified


def _resolve_allowed(url: str) -> tuple[str, str | None]:
    """Check a webhook URL; return (host, a validated address to connect to).

    The address is None when private targets are allowed (no pinning needed).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeWebhookURL(f"Unsupported URL scheme: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise UnsafeWebhookURL("URL has no host")

    if settings.ALLOW_PRIVATE_WEBHOOKS:
        return host, None

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeWebhookURL(f"Could not resolve host: {host}") from exc

    addresses = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _disallowed(ip):
            raise UnsafeWebhookURL(
                f"URL resolves to a disallowed address ({ip}) — refusing to send"
            )
        addresses.append(str(ip))
    if not addresses:
        raise UnsafeWebhookURL(f"Could not resolve host: {host}")
    return host, addresses[0]


def validate_external_url(url: str) -> str:
    """Reject webhook targets that point at internal infrastructure (SSRF).

    Blocks non-http(s) schemes and any host that resolves to a non-public
    address — unless CMA_ALLOW_PRIVATE_WEBHOOKS is set (for pointing alerts at
    a localhost dev endpoint).
    """
    _resolve_allowed(url)
    return url


def _post_json(url: str, payload: dict) -> httpx.Response:
    """POST to the address that was validated, not a fresh DNS answer.

    Resolving twice would let a rebinding DNS server pass the check with a
    public address and then answer with an internal one (SEC-05). TLS still
    verifies against the original host name.
    """
    host, address = _resolve_allowed(url)
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        if address is None:
            resp = client.post(url, json=payload)
        else:
            parsed = urlparse(url)
            netloc = f"[{address}]" if ":" in address else address
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            host_header = f"{host}:{parsed.port}" if parsed.port else host
            request = client.build_request(
                "POST",
                urlunparse(parsed._replace(netloc=netloc)),
                json=payload,
                headers={"Host": host_header},
                extensions={"sni_hostname": host},
            )
            resp = client.send(request)
        resp.raise_for_status()
    return resp


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
        "timestamp": utcnow().isoformat(),
    }
    resp = _post_json(url, payload)
    # The URL path is the secret on most webhook services: log the host only.
    logger.info(
        "Webhook delivered to %s (status %s)", urlparse(url).hostname, resp.status_code
    )


def _send_slack(alert: Alert, config: dict) -> None:
    webhook_url = config.get("webhook_url")
    if not webhook_url:
        logger.error("Slack channel missing 'webhook_url' in config")
        return

    severity_emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(
        alert.severity, "ℹ️"
    )
    text = f"{severity_emoji} *[{alert.severity.upper()}]* {alert.message}"

    resp = _post_json(webhook_url, {"text": text})
    logger.info("Slack message delivered (status %s)", resp.status_code)
