"""Bounded, unauthenticated public HTML ingestion for enrolled sources."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from urllib.parse import urlsplit

from src.core.database import SessionLocal
from src.crawler.http_transport import (
    CrawlerDestinationError, CrawlerNetworkError, fetch_crawler_html,
)
from src.crawler.persistence import create_snapshot_record, persist_snapshot_content
from src.detector.change_detector import check_and_create_drift
from src.models import Entity, MonitoredPage, PageSnapshot
from src.parser.html_cleaner import HTMLCleaner


logger = logging.getLogger(__name__)
MAX_HTML_BYTES = 1_048_576
MAX_EXTRACTED_CHARACTERS = 100_000


class PublicHTMLCrawlFailure(RuntimeError):
    """Source-safe failure code, never a raw upstream error message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PublicHTMLCrawler:
    def __init__(
        self, *, url: str, organization_id: uuid.UUID,
        monitored_page_id: uuid.UUID, claim_token: uuid.UUID | None,
    ) -> None:
        self.url = url
        self.organization_id = organization_id
        self.monitored_page_id = monitored_page_id
        self.claim_token = claim_token

    def crawl(self) -> PageSnapshot | None:
        snapshot_file = None
        persisted = False
        try:
            # Never resolve entity identity from a URL alone. The authorized
            # monitored-page record determines the tenant and page type.
            with SessionLocal() as db:
                row = (
                    db.query(MonitoredPage, Entity)
                    .join(
                        Entity,
                        (Entity.id == MonitoredPage.entity_id)
                        & (Entity.organization_id == MonitoredPage.organization_id),
                    )
                    .filter(
                        MonitoredPage.id == self.monitored_page_id,
                        MonitoredPage.organization_id == self.organization_id,
                        MonitoredPage.url == self.url,
                        MonitoredPage.is_active.is_(True),
                    )
                    .one_or_none()
                )
                if row is None:
                    return None
                page, entity = row
                name, domain, category, page_type = (
                    entity.name, entity.domain, entity.category, page.page_type,
                )
            hostname = urlsplit(self.url).hostname
            if not hostname:
                raise PublicHTMLCrawlFailure("blocked_destination")
            try:
                html = fetch_crawler_html(
                    self.url,
                    allowed_hosts={hostname},
                    headers={"Accept": "text/html, application/xhtml+xml", "Accept-Encoding": "identity"},
                    timeout_seconds=30.0,
                    max_response_bytes=MAX_HTML_BYTES,
                )
            except CrawlerDestinationError as exc:
                raise PublicHTMLCrawlFailure("blocked_destination") from exc
            except CrawlerNetworkError as exc:
                raise PublicHTMLCrawlFailure("fetch_failed") from exc
            content = HTMLCleaner.clean(html)
            if len(content) < 100 or len(content) > MAX_EXTRACTED_CHARACTERS:
                logger.warning("Public HTML extraction outside supported size range.")
                raise PublicHTMLCrawlFailure("content_rejected")
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            snapshot_file = persist_snapshot_content(
                source_name="public_web", page_type=page_type, content=content,
            )
            with SessionLocal() as db:
                snapshot = create_snapshot_record(
                    db=db, organization_id=self.organization_id,
                    monitored_page_id=self.monitored_page_id,
                    claim_token=self.claim_token,
                    entity_name=name, entity_domain=domain,
                    entity_category=category, url=self.url,
                    page_type=page_type, content_hash=content_hash,
                    snapshot_file=snapshot_file,
                )
            persisted = True
            # Persistence is already committed. A detector exception must not
            # convert a successful crawl into a failed crawl/retry, which
            # would create another immutable snapshot for the same observation.
            # PR.16 diagnostics reports a changed pair without a semantic
            # event as missing analysis evidence, not as completed analysis.
            try:
                check_and_create_drift(str(snapshot.monitored_page_id))
            except Exception:
                logger.exception(
                    "Semantic detection failed after snapshot persistence for page %s.",
                    self.monitored_page_id,
                )
            return snapshot
        except PublicHTMLCrawlFailure:
            # The scheduler owns durable outcome recording under the claim.
            raise
        except Exception:
            logger.exception("Public HTML crawl failed for monitored page %s.", self.monitored_page_id)
            return None
        finally:
            if snapshot_file is not None and not persisted:
                try:
                    os.remove(snapshot_file.path)
                except OSError:
                    pass
