"""Single scheduled crawler authority for enrolled public HTML documents.

The stored monitored page owns source identity, classification, and tenant.
Provider-specific manual crawlers must not be selected by hostname: those
adapters have hard-coded page types and can misclassify customer documents.
"""

from __future__ import annotations

import uuid
from typing import Callable
from urllib.parse import urlsplit

from src.crawler.public_html_crawler import PublicHTMLCrawler
from src.models import PageSnapshot


CrawlerRunner = Callable[[], PageSnapshot | None]


class UnsupportedCrawlerSourceError(ValueError):
    """Raised when a monitored URL is not eligible for scheduled HTML fetch."""


def normalize_hostname(url: str) -> str:
    """Validate the stored HTTPS URL before handing it to the safe transport."""
    if not isinstance(url, str) or not url.strip():
        raise UnsupportedCrawlerSourceError("Monitored-page URL cannot be empty.")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise UnsupportedCrawlerSourceError("Invalid monitored-page URL.") from exc

    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
    ):
        raise UnsupportedCrawlerSourceError(
            "Monitored-page URL must be a public HTTPS document URL without "
            "credentials, query, fragment, or custom port."
        )
    return parsed.hostname.lower()


def build_crawler_runner(
    *,
    url: str,
    organization_id: uuid.UUID,
    monitored_page_id: uuid.UUID,
    claim_token: uuid.UUID | None = None,
) -> CrawlerRunner:
    """Build the one tenant-scoped scheduled crawler for customer documents."""
    normalize_hostname(url)
    return PublicHTMLCrawler(
        url=url,
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        claim_token=claim_token,
    ).crawl


def crawl_monitored_page(
    *,
    url: str,
    organization_id: uuid.UUID,
    monitored_page_id: uuid.UUID,
    claim_token: uuid.UUID | None = None,
) -> PageSnapshot | None:
    """Execute one scheduled crawl using its authorized monitored-page row."""
    return build_crawler_runner(
        url=url,
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        claim_token=claim_token,
    )()
