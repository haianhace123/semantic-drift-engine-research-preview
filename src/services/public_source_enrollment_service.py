"""Tenant-scoped enrollment of public HTML pages for scheduled monitoring.

Enrollment never performs network I/O. The crawler independently revalidates
DNS and each redirect at fetch time. This prevents URL validation from being
mistaken for authorization to contact an arbitrary network destination.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models import Entity, MonitoredPage, Organization


_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PAGE_TYPES = frozenset({"pricing", "tos", "privacy", "sla", "docs"})
_FREQUENCIES = frozenset({"hourly", "daily", "weekly"})


class SourceEnrollmentError(ValueError):
    pass


class SourceEnrollmentConflict(SourceEnrollmentError):
    pass


class SourceEnrollmentQuotaExceeded(SourceEnrollmentError):
    pass


@dataclass(frozen=True)
class ValidatedPublicSource:
    url: str
    domain: str
    page_type: str
    crawl_frequency: str
    label: str


def validate_public_source(
    *, url: str, page_type: str, crawl_frequency: str, label: str | None,
) -> ValidatedPublicSource:
    if not isinstance(url, str) or len(url) > 2048:
        raise SourceEnrollmentError("URL must be at most 2048 characters.")
    try:
        parsed = urlsplit(url.strip())
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise SourceEnrollmentError("Invalid URL.") from exc
    if (
        parsed.scheme != "https" or not hostname or parsed.username is not None
        or parsed.password is not None or parsed.query or parsed.fragment
        or port not in (None, 443)
    ):
        raise SourceEnrollmentError(
            "A public HTTPS URL without credentials, query, fragment or custom port is required."
        )
    try:
        hostname = hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise SourceEnrollmentError("Invalid hostname.") from exc
    labels = hostname.split(".")
    if (
        len(hostname) > 253 or len(labels) < 2
        or any(not _HOST_LABEL.fullmatch(part) for part in labels)
        or labels[-1] in {"local", "internal", "localhost", "test", "invalid"}
    ):
        raise SourceEnrollmentError("A public DNS hostname is required.")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise SourceEnrollmentError("IP address URLs are not supported.")
    if page_type not in _PAGE_TYPES or crawl_frequency not in _FREQUENCIES:
        raise SourceEnrollmentError("Unsupported page type or crawl frequency.")
    normalized_path = parsed.path or "/"
    normalized_url = urlunsplit(("https", hostname, normalized_path, "", ""))
    if len(normalized_url) > 2048:
        raise SourceEnrollmentError("URL is too long.")
    normalized_label = (label or hostname).strip()
    if not normalized_label or len(normalized_label) > 255:
        raise SourceEnrollmentError("Source label must be between 1 and 255 characters.")
    return ValidatedPublicSource(
        url=normalized_url,
        domain=hostname,
        page_type=page_type,
        crawl_frequency=crawl_frequency,
        label=normalized_label,
    )


def enroll_public_source(
    db: Session, *, organization_id: uuid.UUID, source: ValidatedPublicSource,
) -> MonitoredPage:
    """Commit one source under an organization lock to serialize quota checks."""
    try:
        organization = (
            db.query(Organization)
            .filter(Organization.id == organization_id, Organization.is_active.is_(True))
            .with_for_update()
            .one_or_none()
        )
        if organization is None:
            raise SourceEnrollmentError("Active organization not found.")
        entity = (
            db.query(Entity)
            .filter(Entity.organization_id == organization_id, Entity.domain == source.domain)
            .one_or_none()
        )
        if entity is not None:
            existing = (
                db.query(MonitoredPage)
                .filter(
                    MonitoredPage.organization_id == organization_id,
                    MonitoredPage.entity_id == entity.id,
                    MonitoredPage.url == source.url,
                )
                .one_or_none()
            )
            if existing is not None:
                raise SourceEnrollmentConflict("This URL is already monitored by the organization.")
        count = (
            db.query(func.count(MonitoredPage.id))
            .filter(MonitoredPage.organization_id == organization_id)
            .scalar()
        )
        if organization.quota_urls is None or count >= organization.quota_urls:
            raise SourceEnrollmentQuotaExceeded("Organization monitored-page quota reached.")
        if entity is None:
            entity = Entity(
                organization_id=organization_id,
                domain=source.domain,
                name=source.label,
                category="Public web",
            )
            db.add(entity)
            db.flush()
        page = MonitoredPage(
            organization_id=organization_id,
            entity_id=entity.id,
            url=source.url,
            page_type=source.page_type,
            crawl_frequency=source.crawl_frequency,
            is_active=True,
            next_crawl_at=None,
        )
        db.add(page)
        db.commit()
        db.refresh(page)
        return page
    except Exception:
        db.rollback()
        raise
