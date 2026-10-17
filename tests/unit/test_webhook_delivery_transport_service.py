from __future__ import annotations

import hashlib
import hmac
import socket
from unittest.mock import MagicMock

import httpcore
import httpx
import pytest

from src.services.webhook_delivery_transport_service import (
    HEADER_DELIVERY_ID,
    HEADER_DELIVERY_KEY,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    PermanentWebhookDeliveryError,
    TransientWebhookDeliveryError,
    _PinnedNetworkBackend,
    _build_pinned_http_transport,
    _validate_webhook_destination,
    build_webhook_headers,
    build_webhook_signature,
    deliver_webhook,
    serialize_webhook_payload,
    validate_webhook_url,
)


PUBLIC_IPV4 = "93.184.216.34"


def _mock_public_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(
        host,
        port,
        *,
        type,
    ):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (PUBLIC_IPV4, port),
            )
        ]

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        fake_getaddrinfo,
    )

def _mock_http_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status_code: int = 200,
    side_effect: Exception | None = None,
) -> tuple[MagicMock, MagicMock]:
    client = MagicMock()
    client_factory = MagicMock(
        return_value=client,
    )

    if side_effect is not None:
        client.post.side_effect = side_effect
    else:
        response = MagicMock()
        response.status_code = status_code
        client.post.return_value = response

    monkeypatch.setattr(
        httpx,
        "Client",
        client_factory,
    )

    return client, client_factory

def test_serialize_webhook_payload_is_deterministic() -> None:
    first = serialize_webhook_payload(
        {
            "z": 1,
            "a": {
                "value": "hello",
            },
        }
    )

    second = serialize_webhook_payload(
        {
            "a": {
                "value": "hello",
            },
            "z": 1,
        }
    )

    assert first == second
    assert first == b'{"a":{"value":"hello"},"z":1}'


def test_serialize_webhook_payload_rejects_invalid_json() -> None:
    with pytest.raises(
        PermanentWebhookDeliveryError,
        match="not JSON serializable",
    ):
        serialize_webhook_payload(
            {
                "invalid": object(),
            }
        )


def test_webhook_signature_authenticates_delivery_identity() -> None:
    body = b'{"event":"alert.created"}'
    timestamp = 1234567890
    secret = "test-secret"

    expected_digest = hmac.new(
        secret.encode("utf-8"),
        (
            b"1234567890."
            b"delivery-id."
            b"delivery-key."
            + body
        ),
        hashlib.sha256,
    ).hexdigest()

    signature = build_webhook_signature(
        secret_key=secret,
        timestamp=timestamp,
        delivery_id="delivery-id",
        delivery_key="delivery-key",
        body=body,
    )

    assert signature == f"sha256={expected_digest}"


def test_webhook_signature_changes_with_delivery_identity() -> None:
    body = b'{"event":"alert.created"}'

    baseline = build_webhook_signature(
        secret_key="secret",
        timestamp=123,
        delivery_id="delivery-id",
        delivery_key="delivery-key",
        body=body,
    )

    different_id = build_webhook_signature(
        secret_key="secret",
        timestamp=123,
        delivery_id="other-delivery-id",
        delivery_key="delivery-key",
        body=body,
    )

    different_key = build_webhook_signature(
        secret_key="secret",
        timestamp=123,
        delivery_id="delivery-id",
        delivery_key="other-delivery-key",
        body=body,
    )

    assert different_id != baseline
    assert different_key != baseline


def test_webhook_headers_include_delivery_identity() -> None:
    body = b'{"event":"alert.created"}'

    headers = build_webhook_headers(
        delivery_id="delivery-id",
        delivery_key="delivery-key",
        secret_key="secret",
        timestamp=123,
        body=body,
    )

    assert headers[HEADER_DELIVERY_ID] == "delivery-id"
    assert headers[HEADER_DELIVERY_KEY] == "delivery-key"
    assert headers[HEADER_TIMESTAMP] == "123"
    assert headers[HEADER_SIGNATURE].startswith("sha256=")
    assert headers["Content-Type"] == "application/json"


def test_validate_webhook_url_accepts_public_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_public_dns(monkeypatch)

    url = "https://example.com/webhook"

    result = validate_webhook_url(url)

    assert result == url


def test_validate_webhook_url_rejects_http() -> None:
    with pytest.raises(
        PermanentWebhookDeliveryError,
        match="must use HTTPS",
    ):
        validate_webhook_url(
            "http://example.com/webhook"
        )


def test_validate_webhook_url_rejects_credentials() -> None:
    with pytest.raises(
        PermanentWebhookDeliveryError,
        match="must not contain credentials",
    ):
        validate_webhook_url(
            "https://user:password@example.com/webhook"
        )


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.10",
        "169.254.169.254",
        "::1",
        "fe80::1",
    ],
)
def test_validate_webhook_url_rejects_non_public_addresses(
    monkeypatch: pytest.MonkeyPatch,
    address: str,
) -> None:
    def fake_getaddrinfo(
        host,
        port,
        *,
        type,
    ):
        if ":" in address:
            return [
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (address, port, 0, 0),
                )
            ]

        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, port),
            )
        ]

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        fake_getaddrinfo,
    )

    with pytest.raises(
        PermanentWebhookDeliveryError,
        match="non-public",
    ):
        validate_webhook_url(
            "https://example.com/webhook"
        )


def test_validate_webhook_url_rejects_mixed_public_private_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(
        host,
        port,
        *,
        type,
    ):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (PUBLIC_IPV4, port),
            ),
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1", port),
            ),
        ]

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        fake_getaddrinfo,
    )

    with pytest.raises(
        PermanentWebhookDeliveryError,
        match="non-public",
    ):
        validate_webhook_url(
            "https://example.com/webhook"
        )


def test_validate_webhook_url_treats_dns_failure_as_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(
        host,
        port,
        *,
        type,
    ):
        raise socket.gaierror("temporary DNS failure")

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        fake_getaddrinfo,
    )

    with pytest.raises(
        TransientWebhookDeliveryError,
        match="could not be resolved",
    ):
        validate_webhook_url(
            "https://example.com/webhook"
        )

def test_pinned_network_backend_connects_only_to_validated_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _PinnedNetworkBackend(
        hostname="example.com",
        port=443,
        addresses=(PUBLIC_IPV4,),
    )

    connect_tcp = MagicMock(
        return_value=MagicMock()
    )

    monkeypatch.setattr(
        backend._delegate,
        "connect_tcp",
        connect_tcp,
    )

    result = backend.connect_tcp(
        "example.com",
        443,
        timeout=5.0,
    )

    assert result is connect_tcp.return_value

    connect_tcp.assert_called_once_with(
        PUBLIC_IPV4,
        443,
        timeout=pytest.approx(5.0),
        local_address=None,
        socket_options=None,
    )


def test_pinned_network_backend_rejects_unexpected_hostname() -> None:
    backend = _PinnedNetworkBackend(
        hostname="example.com",
        port=443,
        addresses=(PUBLIC_IPV4,),
    )

    with pytest.raises(
        httpcore.ConnectError,
        match="unexpected destination",
    ):
        backend.connect_tcp(
            "internal.example",
            443,
        )


def test_pinned_network_backend_rejects_unexpected_port() -> None:
    backend = _PinnedNetworkBackend(
        hostname="example.com",
        port=443,
        addresses=(PUBLIC_IPV4,),
    )

    with pytest.raises(
        httpcore.ConnectError,
        match="unexpected destination",
    ):
        backend.connect_tcp(
            "example.com",
            8443,
        )

def test_deliver_webhook_returns_success_for_2xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_public_dns(monkeypatch)

    client, _ = _mock_http_client(
        monkeypatch,
        status_code=204,
    )

    result = deliver_webhook(
        url="https://example.com/webhook",
        secret_key="secret",
        delivery_id="delivery-id",
        delivery_key="delivery-key",
        payload={
            "event": "alert.created",
        },
        timestamp=123,
    )

    assert result.status_code == 204

    client.post.assert_called_once()

    call = client.post.call_args

    assert (
        call.kwargs["content"]
        == b'{"event":"alert.created"}'
    )

    assert (
        call.kwargs["headers"][HEADER_DELIVERY_ID]
        == "delivery-id"
    )

    assert (
        call.kwargs["headers"][HEADER_DELIVERY_KEY]
        == "delivery-key"
    )


@pytest.mark.parametrize(
    "status_code",
    [
        408,
        409,
        429,
        500,
        502,
        503,
        504,
    ],
)
def test_deliver_webhook_classifies_retryable_http_status(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    _mock_public_dns(monkeypatch)

    _mock_http_client(
        monkeypatch,
        status_code=status_code,
    )

    with pytest.raises(
        TransientWebhookDeliveryError,
        match=f"HTTP {status_code}",
    ):
        deliver_webhook(
            url="https://example.com/webhook",
            secret_key="secret",
            delivery_id="delivery-id",
            delivery_key="delivery-key",
            payload={
                "event": "alert.created",
            },
            timestamp=123,

        )


@pytest.mark.parametrize(
    "status_code",
    [
        400,
        401,
        403,
        404,
        410,
        422,
    ],
)
def test_deliver_webhook_classifies_permanent_http_status(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    _mock_public_dns(monkeypatch)

    _mock_http_client(
        monkeypatch,
        status_code=status_code,
    )

    with pytest.raises(
        PermanentWebhookDeliveryError,
        match=f"HTTP {status_code}",
    ):
        deliver_webhook(
            url="https://example.com/webhook",
            secret_key="secret",
            delivery_id="delivery-id",
            delivery_key="delivery-key",
            payload={
                "event": "alert.created",
            },
            timestamp=123,
        )


def test_deliver_webhook_classifies_transport_error_as_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_public_dns(monkeypatch)

    _mock_http_client(
        monkeypatch,
        side_effect=httpx.ConnectError(
            "connection failed"
        ),
    )

    with pytest.raises(
        TransientWebhookDeliveryError,
        match="transport request failed",
    ):
        deliver_webhook(
            url="https://example.com/webhook",
            secret_key="secret",
            delivery_id="delivery-id",
            delivery_key="delivery-key",
            payload={
                "event": "alert.created",
            },
            timestamp=123,
        )


def test_deliver_webhook_owned_client_enforces_security_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_public_dns(monkeypatch)

    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    client.post.return_value = response

    client_factory = MagicMock(
        return_value=client
    )

    monkeypatch.setattr(
        httpx,
        "Client",
        client_factory,
    )

    deliver_webhook(
        url="https://example.com/webhook",
        secret_key="secret",
        delivery_id="delivery-id",
        delivery_key="delivery-key",
        payload={
            "event": "alert.created",
        },
        timestamp=123,
        timeout_seconds=7.5,
    )

    client_factory.assert_called_once()

    client_kwargs = client_factory.call_args.kwargs

    assert client_kwargs["timeout"] == 7.5
    assert client_kwargs["follow_redirects"] is False
    assert client_kwargs["trust_env"] is False
    assert isinstance(
        client_kwargs["transport"],
        httpx.HTTPTransport,
    )

    client.close.assert_called_once_with()

def test_validated_destination_retains_public_dns_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_public_dns(monkeypatch)

    destination = _validate_webhook_destination(
        "https://example.com/webhook"
    )

    assert destination.url == "https://example.com/webhook"
    assert destination.hostname == "example.com"
    assert destination.port == 443
    assert destination.addresses == (PUBLIC_IPV4,)
