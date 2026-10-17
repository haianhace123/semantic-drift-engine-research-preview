"""
Hardened outbound HTTP transport for production crawlers.

Responsibilities:
- require HTTPS destinations;
- reject embedded URL credentials;
- resolve destinations before network access;
- reject local, private, reserved, and otherwise non-public addresses;
- pin TCP connections to the validated addresses;
- disable automatic redirects and environment-derived proxies;
- revalidate and pin every explicitly followed redirect hop.

The caller remains responsible for source/hostname allowlisting.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx


DEFAULT_CRAWLER_TIMEOUT_SECONDS = 60.0
DEFAULT_CRAWLER_MAX_REDIRECTS = 5


class CrawlerTransportError(RuntimeError):
    """Base error for crawler transport failures."""


class CrawlerDestinationError(CrawlerTransportError):
    """Raised when a crawler destination is not safe to contact."""


class CrawlerNetworkError(CrawlerTransportError):
    """Raised when a safe crawler destination cannot be fetched."""


@dataclass(frozen=True, slots=True)
class _ValidatedCrawlerDestination:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


class _PinnedCrawlerNetworkBackend(httpcore.SyncBackend):
    """
    Connect only to addresses validated for one crawler destination.

    TLS, SNI, and the HTTP Host header continue to use the original
    hostname. Only the TCP destination is replaced with a validated IP.
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
                "Pinned crawler backend requires at least one address."
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
                "Crawler transport attempted an unexpected destination."
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
            "Crawler connection timed out."
        )


def _normalize_hostname(
    hostname: str,
) -> str:
    normalized = str(hostname).strip().rstrip(".")

    try:
        return normalized.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise CrawlerDestinationError(
            "Crawler hostname is invalid."
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
        raise CrawlerNetworkError(
            "Crawler hostname could not be resolved."
        ) from exc

    addresses: set[str] = set()

    for result in results:
        sockaddr = result[4]

        if not sockaddr:
            continue

        address = str(sockaddr[0]).split("%", 1)[0]
        addresses.add(address)

    if not addresses:
        raise CrawlerNetworkError(
            "Crawler hostname resolved to no addresses."
        )

    for address in addresses:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError as exc:
            raise CrawlerDestinationError(
                "Crawler hostname resolved to an invalid address."
            ) from exc

        if not parsed_address.is_global:
            raise CrawlerDestinationError(
                "Crawler destination resolves to a non-public "
                "network address."
            )

    return tuple(sorted(addresses))


def _validate_crawler_destination(
    url: str,
) -> _ValidatedCrawlerDestination:
    normalized_url = str(url).strip()

    if not normalized_url:
        raise CrawlerDestinationError(
            "Crawler URL is empty."
        )

    try:
        parsed = urlsplit(normalized_url)
    except ValueError as exc:
        raise CrawlerDestinationError(
            "Crawler URL is invalid."
        ) from exc

    if parsed.scheme.lower() != "https":
        raise CrawlerDestinationError(
            "Crawler URL must use HTTPS."
        )

    if parsed.username is not None or parsed.password is not None:
        raise CrawlerDestinationError(
            "Crawler URL must not contain credentials."
        )

    hostname = parsed.hostname

    if not hostname:
        raise CrawlerDestinationError(
            "Crawler URL must contain a hostname."
        )

    normalized_hostname = _normalize_hostname(hostname)

    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise CrawlerDestinationError(
            "Crawler URL contains an invalid port."
        ) from exc

    if port < 1 or port > 65535:
        raise CrawlerDestinationError(
            "Crawler URL contains an invalid port."
        )

    addresses = _resolve_public_addresses(
        hostname=normalized_hostname,
        port=port,
    )

    return _ValidatedCrawlerDestination(
        url=normalized_url,
        hostname=normalized_hostname,
        port=port,
        addresses=addresses,
    )


def _build_pinned_http_transport(
    *,
    destination: _ValidatedCrawlerDestination,
) -> httpx.HTTPTransport:
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

    pool._network_backend = _PinnedCrawlerNetworkBackend(
        hostname=destination.hostname,
        port=destination.port,
        addresses=destination.addresses,
    )

    return transport


def fetch_crawler_html(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = DEFAULT_CRAWLER_TIMEOUT_SECONDS,
    max_redirects: int = DEFAULT_CRAWLER_MAX_REDIRECTS,
    allowed_hosts: set[str] | None = None,
    max_response_bytes: int | None = None,
) -> str:
    """
    Fetch one crawler document through the hardened outbound boundary.

    Redirects are followed explicitly so every hop is independently
    validated, resolved, and pinned before network access.
    """
    if timeout_seconds <= 0:
        raise CrawlerDestinationError(
            "Crawler timeout must be greater than zero."
        )

    if max_response_bytes is not None and max_response_bytes < 1:
        raise CrawlerDestinationError("Maximum response size must be positive.")

    if max_redirects < 0:
        raise CrawlerDestinationError(
            "Crawler max redirects must not be negative."
        )

    normalized_allowed_hosts = (
        {
            _normalize_hostname(host)
            for host in allowed_hosts
        }
        if allowed_hosts is not None
        else None
    )

    if (
        normalized_allowed_hosts is not None
        and not normalized_allowed_hosts
    ):
        raise CrawlerDestinationError(
            "Crawler allowed hosts must not be empty."
        )

    current_url = url

    for redirect_count in range(
        max_redirects + 1
    ):
        destination = _validate_crawler_destination(
            current_url
        )
        if max_response_bytes is not None and destination.port != 443:
            raise CrawlerDestinationError("Public HTML crawler requires HTTPS port 443.")

        if (
            normalized_allowed_hosts is not None
            and destination.hostname
            not in normalized_allowed_hosts
        ):
            raise CrawlerDestinationError(
                "Crawler destination host is not allowed."
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
            if max_response_bytes is not None:
                # Streaming bounds decoded content before HTML parsing and
                # avoids holding arbitrarily large third-party responses.
                with client.stream("GET", destination.url, headers=headers) as bounded:
                    if bounded.is_redirect:
                        location = bounded.headers.get("location")
                        if not location:
                            raise CrawlerNetworkError(
                                "Crawler redirect is missing a Location header."
                            )
                        if redirect_count >= max_redirects:
                            raise CrawlerNetworkError("Crawler redirect limit exceeded.")
                        current_url = urljoin(destination.url, location)
                        continue
                    bounded.raise_for_status()
                    content_type = bounded.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type not in {"text/html", "application/xhtml+xml"}:
                        raise CrawlerNetworkError("Crawler response is not HTML.")
                    data = bytearray()
                    for chunk in bounded.iter_bytes():
                        if len(data) + len(chunk) > max_response_bytes:
                            raise CrawlerNetworkError("Crawler response exceeded size limit.")
                        data.extend(chunk)
                    return bytes(data).decode(bounded.encoding or "utf-8", errors="replace")

            response = client.get(
                destination.url,
                headers=headers,
            )

            if response.is_redirect:
                location = response.headers.get(
                    "location"
                )

                if not location:
                    raise CrawlerNetworkError(
                        "Crawler redirect is missing a Location header."
                    )

                if redirect_count >= max_redirects:
                    raise CrawlerNetworkError(
                        "Crawler redirect limit exceeded."
                    )

                current_url = urljoin(
                    destination.url,
                    location,
                )
                continue

            response.raise_for_status()

            return response.text

        except CrawlerTransportError:
            raise

        except httpx.HTTPError as exc:
            raise CrawlerNetworkError(
                "Crawler HTTP request failed."
            ) from exc

        finally:
            client.close()

    raise CrawlerNetworkError(
        "Crawler redirect limit exceeded."
    )
