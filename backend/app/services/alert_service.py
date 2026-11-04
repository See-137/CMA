import ipaddress
import json
import logging
import socket
from urllib.parse import unquote, urlparse, urlunparse
from urllib.request import getproxies_environment, proxy_bypass_environment

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import Alert, AlertChannel
from app.timeutils import utcnow

logger = logging.getLogger(__name__)


class UnsafeWebhookURL(ValueError):
    """Raised when a webhook URL targets a disallowed (private/internal) host."""


class WebhookFailed(RuntimeError):
    """A delivery failed. Names the host only: the URL path is often the secret,
    and httpx's own error messages quote the whole URL."""


# NAT64 embeds an IPv4 address in IPv6: on a NAT64/DNS64 network these reach
# internal IPv4 hosts, and Python counts them as global.
_NAT64 = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)


def _disallowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # is_global excludes private, loopback, link-local, CGNAT (100.64.0.0/10)
    # and documentation ranges. The rest count as global but must not be
    # webhook targets: multicast, reserved (::/8, 4000::/3, ...) and IPv6
    # forms that tunnel to an IPv4 address.
    if not ip.is_global or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return True
    if ip.version == 4:
        return False
    return (
        ip.is_site_local
        or ip.ipv4_mapped is not None
        or ip.sixtofour is not None
        or ip.teredo is not None
        or any(ip in net for net in _NAT64)
    )


def _ascii_host(url: str) -> str:
    """Host as it goes on the wire (IDNA), no brackets; '' when absent."""
    try:
        return httpx.URL(url).raw_host.decode("ascii")
    except (httpx.InvalidURL, ValueError, TypeError) as exc:
        raise UnsafeWebhookURL("Malformed URL") from exc


def _resolve_allowed(url: str) -> tuple[str, list[str]]:
    """Check a webhook URL; return (host, validated addresses to connect to).

    The list is empty when private targets are allowed (no pinning needed).
    Every address must pass: one internal answer rejects the whole host.
    """
    if not isinstance(url, str):
        raise UnsafeWebhookURL("URL must be a string")
    try:
        parsed = urlparse(url)
        parsed.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError as exc:
        raise UnsafeWebhookURL("Malformed URL") from exc
    if parsed.scheme not in ("http", "https"):
        raise UnsafeWebhookURL(f"Unsupported URL scheme: {parsed.scheme or '(none)'}")
    host = _ascii_host(url)
    if not host:
        raise UnsafeWebhookURL("URL has no host")

    if settings.ALLOW_PRIVATE_WEBHOOKS:
        return host, []

    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError) as exc:
        raise UnsafeWebhookURL(f"Could not resolve host: {host}") from exc

    addresses: list[str] = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _disallowed(ip):
            raise UnsafeWebhookURL(
                f"URL resolves to a disallowed address ({ip}) — refusing to send"
            )
        if str(ip) not in addresses:
            addresses.append(str(ip))
    if not addresses:
        raise UnsafeWebhookURL(f"Could not resolve host: {host}")
    return host, addresses


def validate_external_url(url: str) -> str:
    """Reject webhook targets that point at internal infrastructure (SSRF).

    Blocks non-http(s) schemes and any host that resolves to a non-public
    address — unless CMA_ALLOW_PRIVATE_WEBHOOKS is set (for pointing alerts at
    a localhost dev endpoint).
    """
    _resolve_allowed(url)
    return url


def _uses_proxy(scheme: str, host: str) -> bool:
    """Would the environment's proxy settings carry a request to this host?"""
    proxies = getproxies_environment()
    return (scheme in proxies or "all" in proxies) and not proxy_bypass_environment(
        host
    )


def _post_pinned(
    client: httpx.Client, url: str, host: str, addresses: list[str], payload: dict
) -> httpx.Response:
    """POST to an address that was validated, not a fresh DNS answer.

    Resolving twice would let a rebinding DNS server pass the check with a
    public address and then answer with an internal one (SEC-05). TLS still
    verifies against the original host name. Addresses are tried in order on
    connection failures only, so a dual-stack host with no usable IPv6 route
    still gets its alert; a response from the server is final.
    """
    parsed = urlparse(url)
    # The pinned URL has no user:pass@, so credentials go in the header.
    auth = None
    if parsed.username is not None:
        auth = httpx.BasicAuth(unquote(parsed.username), unquote(parsed.password or ""))
    host_only = f"[{host}]" if ":" in host else host
    host_header = f"{host_only}:{parsed.port}" if parsed.port else host_only
    last_error: httpx.TransportError | None = None
    for address in addresses:
        netloc = f"[{address}]" if ":" in address else address
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        request = client.build_request(
            "POST",
            urlunparse(parsed._replace(netloc=netloc)),
            json=payload,
            headers={"Host": host_header},
            extensions={"sni_hostname": host},
        )
        try:
            return client.send(request, auth=auth)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last_error = exc
    raise last_error  # type: ignore[misc]  # addresses is never empty here


def _post_json(url: str, payload: dict) -> httpx.Response:
    """Deliver a webhook; any failure raises ``WebhookFailed`` (no URL in it).

    Behind an egress proxy the proxy resolves the name, so pinning is not
    possible (and a CONNECT to the IP would fail TLS): the check above still
    applies, and the proxy's own policy covers rebinding. Direct connections
    are pinned and ignore proxy settings.
    """
    host, addresses = _resolve_allowed(url)
    proxied = _uses_proxy(urlparse(url).scheme, host)
    try:
        # trust_env=False would also drop SSL_CERT_FILE/SSL_CERT_DIR, which a
        # private or TLS-inspecting CA relies on: keep those for direct calls.
        with httpx.Client(
            timeout=10,
            follow_redirects=False,
            trust_env=proxied or not addresses,
            verify=httpx.create_ssl_context(trust_env=True),
        ) as client:
            if addresses and not proxied:
                resp = _post_pinned(client, url, host, addresses, payload)
            else:
                resp = client.post(url, json=payload)
            # Redirects are not followed: a 3xx is not a delivery either.
            if not resp.is_success:
                raise WebhookFailed(f"{host} returned HTTP {resp.status_code}")
    except httpx.HTTPError as exc:
        # The reason tells a bad certificate from a refused connection. Any
        # copy of the path is removed: it is often the secret.
        path = urlparse(url).path
        reason = str(exc)[:200]
        if path and path != "/":
            reason = reason.replace(path, "/***")
        raise WebhookFailed(f"{host}: {type(exc).__name__}: {reason}") from None
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
