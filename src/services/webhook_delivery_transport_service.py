"""
HTTP transport for durable webhook Alert deliveries.

Responsibilities:
- validate webhook destination URLs before network access;
- block local, private, reserved, and otherwise non-public targets;
- serialize payloads deterministically;
- sign webhook requests with HMAC-SHA256;
- classify HTTP and transport failures as transient or permanent.

Non-responsibilities:
- no database queries or writes;
- no AlertDelivery state transitions;
- no retry scheduling;
- no commit / rollback.

The delivery worker owns database transaction boundaries.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

import httpcore
import httpx


DEFAULT_WEBHOOK_TIMEOUT_SECONDS = 10.0

RETRYABLE_HTTP_STATUS_CODES = frozenset(
    {
        408,
        409,
        429,
        500,
        502,
        503,
        504,
    }
)

WEBHOOK_USER_AGENT = "semantic-drift-engine-webhook/1"

HEADER_DELIVERY_ID = "X-SDE-Delivery-ID"
HEADER_DELIVERY_KEY = "X-SDE-Delivery-Key"
HEADER_TIMESTAMP = "X-SDE-Timestamp"
HEADER_SIGNATURE = "X-SDE-Signature"

SIGNATURE_PREFIX = "sha256="


class WebhookDeliveryTransportError(RuntimeError):
    """Base error for webhook transport failures."""


class PermanentWebhookDeliveryError(
    WebhookDeliveryTransportError
):
    """Raised when retrying the same request should not help."""


class TransientWebhookDeliveryError(
    WebhookDeliveryTransportError
):
    """Raised when the delivery may succeed on a later retry."""


@dataclass(frozen=True)
class WebhookDeliveryResponse:
    status_code: int


@dataclass(frozen=True)
class _ValidatedWebhookDestination:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


class _PinnedNetworkBackend(httpcore.SyncBackend):
    """
    Connect only to addresses validated for one webhook destination.

    httpcore still owns TLS negotiation and receives the original request
    hostname, so certificate verification and SNI remain hostname-based.
    Only the TCP destination is replaced with a prevalidated IP address.
    """

    def __init__(
        self,
        *,
        hostname: str,
        port: int,
        addresses: tuple[str, ...],
    ) -> None:
        if not addresses:
            raise ValueError(
                "Pinned webhook backend requires at least one address."
            )

        self._hostname = _normalize_hostname(hostname)
        self._port = int(port)
        self._addresses = addresses
        self._delegate = httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        normalized_host = _normalize_hostname(host)

        if (
            normalized_host != self._hostname
            or int(port) != self._port
        ):
            raise httpcore.ConnectError(
                "Webhook transport attempted an unexpected destination."
            )

        started_at = time.monotonic()
        last_error: Exception | None = None

        for address in self._addresses:
            remaining_timeout = timeout

            if timeout is not None:
                elapsed = time.monotonic() - started_at
                remaining_timeout = max(
                    0.0,
                    timeout - elapsed,
                )

                if remaining_timeout <= 0:
                    break

            try:
                return self._delegate.connect_tcp(
                    address,
                    port,
                    timeout=remaining_timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (
                httpcore.ConnectError,
                httpcore.ConnectTimeout,
            ) as exc:
                last_error = exc

        if last_error is not None:
            raise last_error

        raise httpcore.ConnectTimeout(
            "Webhook connection timed out."
        )

def serialize_webhook_payload(
    payload: Mapping[str, object],
) -> bytes:
    """
    Serialize a webhook payload into stable UTF-8 JSON bytes.

    Stable bytes are required because the exact request body is part
    of the HMAC signature.
    """
    try:
        serialized = json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PermanentWebhookDeliveryError(
            "Webhook payload is not JSON serializable."
        ) from exc

    return serialized.encode("utf-8")


def build_webhook_signature(
    *,
    secret_key: str,
    timestamp: int,
    delivery_id: str,
    delivery_key: str,
    body: bytes,
) -> str:
    """
    Return an HMAC-SHA256 signature for delivery identity and body.

    Receivers can reject old timestamps before verifying the
    signature to reduce replay risk. The authenticated delivery
    identity can be used to deduplicate at-least-once delivery.
    """
    normalized_secret = str(secret_key).strip()
    normalized_delivery_id = str(delivery_id).strip()
    normalized_delivery_key = str(delivery_key).strip()

    if not normalized_secret:
        raise PermanentWebhookDeliveryError(
            "Webhook secret key is empty."
        )

    if timestamp < 0:
        raise PermanentWebhookDeliveryError(
            "Webhook timestamp must not be negative."
        )

    if not normalized_delivery_id:
        raise PermanentWebhookDeliveryError(
            "Webhook delivery id is empty."
        )

    if not normalized_delivery_key:
        raise PermanentWebhookDeliveryError(
            "Webhook delivery key is empty."
        )

    signed_payload = (
        str(timestamp).encode("ascii")
        + b"."
        + normalized_delivery_id.encode("utf-8")
        + b"."
        + normalized_delivery_key.encode("utf-8")
        + b"."
        + body
    )

    digest = hmac.new(
        normalized_secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()

    return f"{SIGNATURE_PREFIX}{digest}"


def build_webhook_headers(
    *,
    delivery_id: str,
    delivery_key: str,
    secret_key: str,
    timestamp: int,
    body: bytes,
) -> dict[str, str]:
    """Build stable webhook delivery and signature headers."""
    normalized_delivery_id = str(delivery_id).strip()
    normalized_delivery_key = str(delivery_key).strip()

    if not normalized_delivery_id:
        raise PermanentWebhookDeliveryError(
            "Webhook delivery id is empty."
        )

    if not normalized_delivery_key:
        raise PermanentWebhookDeliveryError(
            "Webhook delivery key is empty."
        )

    signature = build_webhook_signature(
        secret_key=secret_key,
        timestamp=timestamp,
        delivery_id=normalized_delivery_id,
        delivery_key=normalized_delivery_key,
        body=body,
    )

    return {
        "Content-Type": "application/json",
        "User-Agent": WEBHOOK_USER_AGENT,
        HEADER_DELIVERY_ID: normalized_delivery_id,
        HEADER_DELIVERY_KEY: normalized_delivery_key,
        HEADER_TIMESTAMP: str(timestamp),
        HEADER_SIGNATURE: signature,
    }

def _normalize_hostname(hostname: str) -> str:
    normalized = str(hostname).strip().rstrip(".")

    try:
        return normalized.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise PermanentWebhookDeliveryError(
            "Webhook hostname is invalid."
        ) from exc

def _resolve_public_addresses(
    *,
    hostname: str,
    port: int,
) -> tuple[str, ...]:
    try:
        results = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise TransientWebhookDeliveryError(
            "Webhook hostname could not be resolved."
        ) from exc

    addresses: set[str] = set()

    for result in results:
        sockaddr = result[4]

        if not sockaddr:
            continue

        address = str(sockaddr[0]).split("%", 1)[0]
        addresses.add(address)

    if not addresses:
        raise TransientWebhookDeliveryError(
            "Webhook hostname resolved to no addresses."
        )

    for address in addresses:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError as exc:
            raise PermanentWebhookDeliveryError(
                "Webhook hostname resolved to an invalid address."
            ) from exc

        if not parsed_address.is_global:
            raise PermanentWebhookDeliveryError(
                "Webhook destination resolves to a non-public "
                "network address."
            )

    return tuple(sorted(addresses))


def _validate_webhook_destination(
    url: str,
) -> _ValidatedWebhookDestination:
    normalized_url = str(url).strip()

    if not normalized_url:
        raise PermanentWebhookDeliveryError(
            "Webhook URL is empty."
        )

    try:
        parsed = urlsplit(normalized_url)
    except ValueError as exc:
        raise PermanentWebhookDeliveryError(
            "Webhook URL is invalid."
        ) from exc

    if parsed.scheme.lower() != "https":
        raise PermanentWebhookDeliveryError(
            "Webhook URL must use HTTPS."
        )

    if parsed.username is not None or parsed.password is not None:
        raise PermanentWebhookDeliveryError(
            "Webhook URL must not contain credentials."
        )

    hostname = parsed.hostname

    if not hostname:
        raise PermanentWebhookDeliveryError(
            "Webhook URL must contain a hostname."
        )

    normalized_hostname = _normalize_hostname(hostname)

    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise PermanentWebhookDeliveryError(
            "Webhook URL contains an invalid port."
        ) from exc

    if port < 1 or port > 65535:
        raise PermanentWebhookDeliveryError(
            "Webhook URL contains an invalid port."
        )

    addresses = _resolve_public_addresses(
        hostname=normalized_hostname,
        port=port,
    )

    return _ValidatedWebhookDestination(
        url=normalized_url,
        hostname=normalized_hostname,
        port=port,
        addresses=addresses,
    )


def validate_webhook_url(url: str) -> str:
    """
    Validate a webhook URL before outbound network access.

    Only HTTPS destinations resolving exclusively to public network
    addresses are accepted. Redirects are disabled separately by the
    HTTP transport.
    """
    return _validate_webhook_destination(url).url

def _build_pinned_http_transport(
    *,
    destination: _ValidatedWebhookDestination,
) -> httpx.HTTPTransport:
    """
    Build an HTTPX transport whose TCP connections are restricted to
    the destination addresses validated for this delivery attempt.

    HTTPX 0.27.x and httpcore 1.0.x are pinned by the application.
    The guarded pool backend replacement is intentional: it preserves
    HTTPX's normal TLS, SNI, Host header, exception mapping, and
    response-stream behavior while removing the second DNS lookup.
    """
    transport = httpx.HTTPTransport(
        trust_env=False,
    )

    pool = getattr(
        transport,
        "_pool",
        None,
    )

    if (
        pool is None
        or not hasattr(pool, "_network_backend")
    ):
        transport.close()
        raise RuntimeError(
            "Unsupported HTTPX/httpcore transport internals."
        )

    pool._network_backend = _PinnedNetworkBackend(
        hostname=destination.hostname,
        port=destination.port,
        addresses=destination.addresses,
    )

    return transport

def deliver_webhook(
    *,
    url: str,
    secret_key: str,
    delivery_id: str,
    delivery_key: str,
    payload: Mapping[str, object],
    timestamp: int | None = None,
    timeout_seconds: float = DEFAULT_WEBHOOK_TIMEOUT_SECONDS,
) -> WebhookDeliveryResponse:
    """
    Deliver one webhook request.

    The destination is resolved and validated once per attempt. TCP
    connections are restricted to those validated addresses while the
    original hostname remains authoritative for HTTP and TLS.

    HTTP redirects and environment-derived proxies are disabled.
    """
    if timeout_seconds <= 0:
        raise PermanentWebhookDeliveryError(
            "Webhook timeout must be greater than zero."
        )

    destination = _validate_webhook_destination(url)

    body = serialize_webhook_payload(payload)

    effective_timestamp = (
        int(time.time())
        if timestamp is None
        else int(timestamp)
    )

    headers = build_webhook_headers(
        delivery_id=delivery_id,
        delivery_key=delivery_key,
        secret_key=secret_key,
        timestamp=effective_timestamp,
        body=body,
    )

    transport = _build_pinned_http_transport(
        destination=destination,
    )

    client = httpx.Client(
        timeout=timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    )

    try:
        response = client.post(
            destination.url,
            content=body,
            headers=headers,
        )

    except httpx.TransportError as exc:
        raise TransientWebhookDeliveryError(
            "Webhook transport request failed."
        ) from exc

    finally:
        client.close()

    status_code = int(response.status_code)

    if 200 <= status_code < 300:
        return WebhookDeliveryResponse(
            status_code=status_code,
        )

    if status_code in RETRYABLE_HTTP_STATUS_CODES:
        raise TransientWebhookDeliveryError(
            f"Webhook endpoint returned HTTP {status_code}."
        )

    raise PermanentWebhookDeliveryError(
        f"Webhook endpoint returned HTTP {status_code}."
    )