"""
Slack Terms of Service crawler.

Fetches, normalizes and persists tenant-scoped snapshots of Slack's
Terms of Service.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import uuid

sys.path.append(
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(
                os.path.abspath(__file__)
            )
        )
    )
)

from src.core.database import SessionLocal
from src.core.org_utils import resolve_organization_id
from src.crawler.http_transport import (
    CrawlerTransportError,
    fetch_crawler_html,
)
from src.crawler.persistence import (
    SnapshotFile,
    create_snapshot_record,
    persist_snapshot_content,
)
from src.detector.change_detector import check_and_create_drift
from src.models import PageSnapshot
from src.parser.html_cleaner import HTMLCleaner


logger = logging.getLogger(__name__)


class SlackCrawler:
    """Crawler for Slack's Terms of Service."""

    def __init__(
        self,
        url: str = "https://slack.com/terms-of-service",
        organization_id: str | uuid.UUID | None = None,
        monitored_page_id: uuid.UUID | None = None,
        claim_token: uuid.UUID | None = None,
    ) -> None:
        self.url = url
        self.monitored_page_id = monitored_page_id
        self.claim_token = claim_token
        self.domain = "slack.com"
        self.page_type = "tos"
        self.entity_name = "Slack"
        self.entity_category = "SaaS"

        self.organization_id = (
            resolve_organization_id(
                organization_id
            )
        )

    def crawl(
        self,
    ) -> PageSnapshot | None:
        """Execute the complete tenant-scoped crawl pipeline."""
        snapshot_file: SnapshotFile | None = None
        snapshot_persisted = False

        try:
            html_content = self._fetch_html()

            if not html_content:
                logger.error(
                    "Slack crawl returned no HTML."
                )
                return None

            cleaned_content = HTMLCleaner.clean(
                html_content
            )

            if (
                not cleaned_content
                or len(
                    cleaned_content.strip()
                )
                < 100
            ):
                logger.error(
                    "Slack normalized content is too short; "
                    "the page structure may have changed."
                )
                return None

            content_hash = (
                self._compute_hash(
                    cleaned_content
                )
            )

            snapshot_file = (
                persist_snapshot_content(
                    source_name="slack",
                    page_type=self.page_type,
                    content=cleaned_content,
                )
            )

            snapshot = (
                self._save_to_database(
                    content_hash=content_hash,
                    snapshot_file=snapshot_file,
                )
            )

            if snapshot is None:
                self._remove_orphaned_snapshot(
                    snapshot_file
                )
                return None

            snapshot_persisted = True

            logger.info(
                "Slack snapshot persisted path=%s hash=%s snapshot_id=%s",
                snapshot_file.path,
                content_hash[:16],
                snapshot.id,
            )

            check_and_create_drift(
                str(
                    snapshot.monitored_page_id
                )
            )

            return snapshot

        except Exception:
            if (
                snapshot_file is not None
                and not snapshot_persisted
            ):
                self._remove_orphaned_snapshot(
                    snapshot_file
                )

            logger.exception(
                "Unexpected error while crawling Slack."
            )

            return None

    def _fetch_html(
        self,
    ) -> str | None:
        """Fetch raw HTML through the hardened crawler transport."""
        try:
            return fetch_crawler_html(
                self.url,
                timeout_seconds=60.0,
            )

        except CrawlerTransportError:
            logger.exception(
                "Failed to fetch Slack HTML from %s.",
                self.url,
            )

            return None

    @staticmethod
    def _compute_hash(
        content: str,
    ) -> str:
        """Return the SHA-256 digest of normalized content."""
        return hashlib.sha256(
            content.encode(
                "utf-8"
            )
        ).hexdigest()

    def _save_to_database(
        self,
        *,
        content_hash: str,
        snapshot_file: SnapshotFile,
    ) -> PageSnapshot | None:
        """Persist tenant-scoped snapshot metadata."""
        db = SessionLocal()

        try:
            return create_snapshot_record(
                db=db,
                organization_id=self.organization_id,
                entity_name=self.entity_name,
                entity_domain=self.domain,
                entity_category=self.entity_category,
                url=self.url,
                page_type=self.page_type,
                content_hash=content_hash,
                snapshot_file=snapshot_file,
                monitored_page_id=self.monitored_page_id,
                claim_token=self.claim_token,
            )

        except Exception:
            db.rollback()

            logger.exception(
                "Failed to persist Slack snapshot metadata."
            )

            return None

        finally:
            db.close()

    @staticmethod
    def _remove_orphaned_snapshot(
        snapshot_file: SnapshotFile,
    ) -> None:
        """Remove an unreferenced filesystem snapshot."""
        try:
            if os.path.exists(
                snapshot_file.path
            ):
                os.remove(
                    snapshot_file.path
                )

        except OSError:
            logger.warning(
                "Failed to remove orphaned Slack snapshot: %s",
                snapshot_file.path,
            )


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s - "
            "%(name)s - "
            "%(levelname)s - "
            "%(message)s"
        ),
    )

    SlackCrawler().crawl()


def crawl(
    organization_id: str | uuid.UUID | None = None,
) -> PageSnapshot | None:
    """Compatibility wrapper for subprocess integrations."""
    return SlackCrawler(
        organization_id=organization_id
    ).crawl()


if __name__ == "__main__":
    main()