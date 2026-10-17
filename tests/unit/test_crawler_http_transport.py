from __future__ import annotations

import socket
from unittest.mock import MagicMock

import httpcore
import httpx
import pytest

from src.crawler.http_transport import (
    CrawlerDestinationError,
    CrawlerNetworkError,
    _PinnedCrawlerNetworkBackend,
    _build_pinned_http_transport,
    _validate_crawler_destination,
    fetch_crawler_html,
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


def test_validate_crawler_destination_accepts_public_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_public_dns(monkeypatch)

    destination = _validate_crawler_destination(
        "https://openai.com/policies/terms-of-use"
    )

    assert destination.url == (
        "https://openai.com/policies/terms-of-use"
    )
    assert destination.hostname == "openai.com"
    assert destination.port == 443
    assert destination.addresses == (PUBLIC_IPV4,)


@pytest.mark.parametrize(
    "url",
    [
        "http://openai.com/policies/terms-of-use",
        "ftp://openai.com/policies/terms-of-use",
    ],
)
def test_validate_crawler_destination_requires_https(
    url: str,
) -> None:
    with pytest.raises(
        CrawlerDestinationError,
        match="must use HTTPS",
    ):
        _validate_crawler_destination(
            url
        )


def test_validate_crawler_destination_rejects_credentials() -> None:
    with pytest.raises(
        CrawlerDestinationError,
        match="must not contain credentials",
    ):
        _validate_crawler_destination(
            "https://user:password@openai.com/policies/terms-of-use"
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
def test_validate_crawler_destination_rejects_non_public_addresses(
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
        CrawlerDestinationError,
        match="non-public",
    ):
        _validate_crawler_destination(
            "https://openai.com/policies/terms-of-use"
        )


def test_validate_crawler_destination_rejects_mixed_public_private_dns(
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
        CrawlerDestinationError,
        match="non-public",
    ):
        _validate_crawler_destination(
            "https://openai.com/policies/terms-of-use"
        )


def test_pinned_backend_rejects_unexpected_destination() -> None:
    backend = _PinnedCrawlerNetworkBackend(
        hostname="openai.com",
        port=443,
        addresses=(PUBLIC_IPV4,),
    )

    with pytest.raises(
        httpcore.ConnectError,
        match="unexpected destination",
    ):
        backend.connect_tcp(
            "127.0.0.1",
            443,
            timeout=1.0,
        )


def test_pinned_backend_connects_to_validated_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _PinnedCrawlerNetworkBackend(
        hostname="openai.com",
        port=443,
        addresses=(PUBLIC_IPV4,),
    )

    connection = object()
    connect_tcp = MagicMock(
        return_value=connection
    )

    monkeypatch.setattr(
        backend._delegate,
        "connect_tcp",
        connect_tcp,
    )

    result = backend.connect_tcp(
        "openai.com",
        443,
        timeout=5.0,
    )

    assert result is connection

    connect_tcp.assert_called_once()

    args = connect_tcp.call_args.args
    kwargs = connect_tcp.call_args.kwargs

    assert args[0] == PUBLIC_IPV4
    assert args[1] == 443
    assert kwargs["timeout"] <= 5.0


def test_fetch_crawler_html_disables_redirects_and_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_public_dns(monkeypatch)

    response = MagicMock()
    response.is_redirect = False
    response.text = "<html>safe</html>"

    client = MagicMock()
    client.get.return_value = response

    client_factory = MagicMock(
        return_value=client,
    )

    transport = MagicMock(
        spec=httpx.HTTPTransport
    )

    monkeypatch.setattr(
        httpx,
        "HTTPTransport",
        MagicMock(
            return_value=transport,
        ),
    )

    monkeypatch.setattr(
        httpx,
        "Client",
        client_factory,
    )

    monkeypatch.setattr(
        "src.crawler.http_transport._build_pinned_http_transport",
        lambda *, destination: transport,
    )

    result = fetch_crawler_html(
        "https://openai.com/policies/terms-of-use",
        timeout_seconds=30.0,
    )

    assert result == "<html>safe</html>"

    client_factory.assert_called_once_with(
        timeout=30.0,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    )

    client.get.assert_called_once_with(
        "https://openai.com/policies/terms-of-use",
        headers=None,
    )

    response.raise_for_status.assert_called_once_with()
    client.close.assert_called_once_with()


def test_fetch_crawler_html_rejects_unsafe_redirect_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_public_dns(monkeypatch)

    response = MagicMock()
    response.is_redirect = True
    response.headers = {
        "location": (
            "http://169.254.169.254/latest/meta-data/"
        )
    }

    client = MagicMock()
    client.get.return_value = response

    transport = MagicMock(
        spec=httpx.HTTPTransport
    )

    monkeypatch.setattr(
        "src.crawler.http_transport._build_pinned_http_transport",
        lambda *, destination: transport,
    )

    monkeypatch.setattr(
        httpx,
        "Client",
        MagicMock(
            return_value=client,
        ),
    )

    with pytest.raises(
        CrawlerDestinationError,
        match="must use HTTPS",
    ):
        fetch_crawler_html(
            "https://openai.com/source"
        )

    client.get.assert_called_once()
    client.close.assert_called_once_with()


def test_fetch_crawler_html_follows_safe_redirect_with_revalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_public_dns(monkeypatch)

    redirect_response = MagicMock()
    redirect_response.is_redirect = True
    redirect_response.headers = {
        "location": "/main-services-agreement"
    }

    final_response = MagicMock()
    final_response.is_redirect = False
    final_response.text = "<html>safe</html>"

    first_client = MagicMock()
    first_client.get.return_value = redirect_response

    second_client = MagicMock()
    second_client.get.return_value = final_response

    transport = MagicMock(
        spec=httpx.HTTPTransport
    )

    monkeypatch.setattr(
        "src.crawler.http_transport._build_pinned_http_transport",
        lambda *, destination: transport,
    )

    client_factory = MagicMock(
        side_effect=[
            first_client,
            second_client,
        ]
    )

    monkeypatch.setattr(
        httpx,
        "Client",
        client_factory,
    )

    result = fetch_crawler_html(
        "https://slack.com/terms-of-service"
    )

    assert result == "<html>safe</html>"

    first_client.get.assert_called_once_with(
        "https://slack.com/terms-of-service",
        headers=None,
    )

    second_client.get.assert_called_once_with(
        "https://slack.com/main-services-agreement",
        headers=None,
    )

    final_response.raise_for_status.assert_called_once_with()
    first_client.close.assert_called_once_with()
    second_client.close.assert_called_once_with()


def test_fetch_crawler_html_rejects_redirect_outside_allowed_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_public_dns(monkeypatch)

    redirect_response = MagicMock()
    redirect_response.is_redirect = True
    redirect_response.headers = {
        "location": "https://example.com/escape"
    }

    client = MagicMock()
    client.get.return_value = redirect_response

    transport = MagicMock(
        spec=httpx.HTTPTransport
    )

    monkeypatch.setattr(
        "src.crawler.http_transport._build_pinned_http_transport",
        lambda *, destination: transport,
    )

    monkeypatch.setattr(
        httpx,
        "Client",
        MagicMock(
            return_value=client,
        ),
    )

    with pytest.raises(
        CrawlerDestinationError,
        match="host is not allowed",
    ):
        fetch_crawler_html(
            "https://openai.com/source",
            allowed_hosts={"openai.com"},
        )

    client.get.assert_called_once()
    client.close.assert_called_once_with()
    client.close.assert_called_once_with()
