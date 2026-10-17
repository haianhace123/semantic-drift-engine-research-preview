"""
OpenAI Terms of Use crawler.

Fetches, normalizes and persists tenant-scoped snapshots of OpenAI's
Terms of Use.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import uuid

from bs4 import BeautifulSoup

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


class OpenAICrawler:
    """Crawler for OpenAI's Terms of Use."""

    def __init__(
        self,
        url: str = "https://openai.com/policies/terms-of-use",
        organization_id: str | uuid.UUID | None = None,
        monitored_page_id: uuid.UUID | None = None,
        claim_token: uuid.UUID | None = None,
    ) -> None:
        self.url = url
        self.monitored_page_id = monitored_page_id
        self.claim_token = claim_token
        self.domain = "openai.com"
        self.page_type = "tos"
        self.entity_name = "OpenAI"
        self.entity_category = "AI Technology"

        self.organization_id = (
            resolve_organization_id(
                organization_id
            )
        )

        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        }

    def crawl(
        self,
    ) -> PageSnapshot | None:
        """Fetch and persist the current OpenAI Terms of Use."""
        snapshot_file: SnapshotFile | None = None
        snapshot_persisted = False

        try:
            html_content = self._fetch_html()

            if not html_content:
                logger.error(
                    "OpenAI crawl returned no HTML."
                )
                return None

            cleaned_content = (
                self._normalize_content(
                    html_content
                )
            )

            if (
                not cleaned_content
                or len(
                    cleaned_content.strip()
                )
                < 100
            ):
                logger.error(
                    "OpenAI normalized content is too short."
                )
                return None

            content_hash = (
                self._compute_hash(
                    cleaned_content
                )
            )

            snapshot_file = (
                persist_snapshot_content(
                    source_name="openai",
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
                "OpenAI snapshot persisted path=%s hash=%s snapshot_id=%s",
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
                "Unexpected error while crawling OpenAI."
            )

            return None

    def _fetch_html(
        self,
    ) -> str | None:
        """Fetch raw HTML through the hardened crawler transport."""
        try:
            return fetch_crawler_html(
                self.url,
                headers=self.headers,
                timeout_seconds=30.0,
                allowed_hosts={"openai.com"},
            )

        except CrawlerTransportError:
            logger.exception(
                "Failed to fetch OpenAI Terms of Use from %s.",
                self.url,
            )

            return None

    @staticmethod
    def _normalize_content(
        html_content: str,
    ) -> str:
        """
        Normalize HTML, falling back to visible text when cleaner output
        is unexpectedly short.
        """
        cleaned_content = HTMLCleaner.clean(
            html_content
        )

        if (
            cleaned_content
            and len(
                cleaned_content.strip()
            )
            >= 100
        ):
            return cleaned_content

        logger.warning(
            "OpenAI HTML cleaner produced insufficient content; "
            "falling back to visible text."
        )

        soup = BeautifulSoup(
            html_content,
            "html.parser",
        )

        return soup.get_text(
            separator="\n",
            strip=True,
        )

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
                "Failed to persist OpenAI snapshot metadata."
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
                "Failed to remove orphaned OpenAI snapshot: %s",
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

    OpenAICrawler().crawl()


def crawl(
    organization_id: str | uuid.UUID | None = None,
) -> PageSnapshot | None:
    """Compatibility wrapper for subprocess integrations."""
    return OpenAICrawler(
        organization_id=organization_id
    ).crawl()


if __name__ == "__main__":
    main()