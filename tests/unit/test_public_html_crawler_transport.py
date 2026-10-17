from __future__ import annotations

import socket
from unittest.mock import MagicMock

import httpx
import pytest

from src.crawler.http_transport import (
    CrawlerDestinationError, CrawlerNetworkError, fetch_crawler_html,
)


def _install_transport(monkeypatch, *, chunks, content_type="text/html", redirect=None):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *, type: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
    ])
    response = MagicMock()
    response.is_redirect = redirect is not None
    response.headers = {"content-type": content_type, **({"location": redirect} if redirect else {})}
    response.encoding = "utf-8"
    response.iter_bytes.return_value = iter(chunks)
    context = MagicMock()
    context.__enter__.return_value = response
    client = MagicMock()
    client.stream.return_value = context
    transport = MagicMock(spec=httpx.HTTPTransport)
    monkeypatch.setattr("src.crawler.http_transport._build_pinned_http_transport", lambda *, destination: transport)
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=client))
    return client


def test_public_html_is_bounded_before_full_read(monkeypatch):
    client = _install_transport(monkeypatch, chunks=[b"<html>", b"a" * 1024])
    with pytest.raises(CrawlerNetworkError, match="size limit"):
        fetch_crawler_html("https://docs.example.org/policy", max_response_bytes=500, allowed_hosts={"docs.example.org"})
    client.close.assert_called_once()


def test_public_html_rejects_non_html(monkeypatch):
    _install_transport(monkeypatch, chunks=[b"%PDF"], content_type="application/pdf")
    with pytest.raises(CrawlerNetworkError, match="not HTML"):
        fetch_crawler_html("https://docs.example.org/report.pdf", max_response_bytes=500)


def test_public_html_reads_bounded_text(monkeypatch):
    _install_transport(monkeypatch, chunks=[b"<html>", b"safe</html>"])
    assert fetch_crawler_html(
        "https://docs.example.org/page", max_response_bytes=100,
        allowed_hosts={"docs.example.org"},
    ) == "<html>safe</html>"


def test_public_html_rejects_external_redirect(monkeypatch):
    _install_transport(monkeypatch, chunks=[], redirect="https://attacker.example.net/")
    with pytest.raises(CrawlerDestinationError, match="not allowed"):
        fetch_crawler_html("https://docs.example.org/page", max_response_bytes=100, allowed_hosts={"docs.example.org"})


def test_public_html_rejects_redirect_to_nonstandard_port(monkeypatch):
    _install_transport(monkeypatch, chunks=[], redirect="https://docs.example.org:8080/")
    with pytest.raises(CrawlerDestinationError, match="port 443"):
        fetch_crawler_html("https://docs.example.org/page", max_response_bytes=100, allowed_hosts={"docs.example.org"})
