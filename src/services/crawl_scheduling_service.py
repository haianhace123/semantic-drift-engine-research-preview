from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.models.monitored_page import MonitoredPage


DEFAULT_CRAWL_CLAIM_LEASE_SECONDS = 900
DEFAULT_CRAWL_FAILURE_RETRY_SECONDS = 300

# Public, stable classification values only. Do not persist exception messages,
# HTTP response bodies, DNS answers or other tenant/source secrets here.
CRAWL_FAILURE_CODES = frozenset({
    "blocked_destination", "fetch_failed", "content_rejected",
    "unsupported_source", "snapshot_unavailable", "internal_error",
})


_CRAWL_FREQUENCY_INTERVALS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
}


@dataclass(frozen=True, slots=True)
class CrawlClaim:
    monitored_page_id: uuid.UUID
    organization_id: uuid.UUID
    url: str
    crawl_frequency: str
    claim_token: uuid.UUID
    claimed_at: datetime
    claim_expires_at: datetime


class CrawlSchedulingError(RuntimeError):
    """Base error for crawl scheduling failures."""


class CrawlSchedulingConfigurationError(
    CrawlSchedulingError
):
    """Raised when crawl scheduling parameters are invalid."""


class CrawlClaimAuthorityError(
    CrawlSchedulingError
):
    """Raised when a crawl claim no longer has finalization authority."""


def claim_due_monitored_page(
    db: Session,
    *,
    organization_id: uuid.UUID | None = None,
    lease_seconds: int = DEFAULT_CRAWL_CLAIM_LEASE_SECONDS,
) -> CrawlClaim | None:
    """
    Atomically claim one monitored page that is eligible for crawling.

    The caller owns commit / rollback and must commit the claim before
    performing network, browser, or other external work.
    """
    if lease_seconds < 1:
        raise CrawlSchedulingConfigurationError(
            "Crawl claim lease seconds must be at least 1."
        )

    effective_now = db.execute(
        select(
            func.clock_timestamp()
        )
    ).scalar_one()

    query = (
        db.query(MonitoredPage)
        .filter(
            MonitoredPage.is_active.is_(True),
            or_(
                MonitoredPage.next_crawl_at.is_(None),
                MonitoredPage.next_crawl_at
                <= effective_now,
            ),
            or_(
                MonitoredPage.crawl_claim_token.is_(None),
                MonitoredPage.crawl_claim_expires_at
                <= effective_now,
            ),
        )
    )

    if organization_id is not None:
        query = query.filter(
            MonitoredPage.organization_id
            == organization_id
        )

    page = (
        query
        .order_by(
            MonitoredPage.next_crawl_at.asc().nullsfirst(),
            MonitoredPage.id.asc(),
        )
        .with_for_update(
            skip_locked=True
        )
        .first()
    )

    if page is None:
        return None

    claim_token = uuid.uuid4()
    claim_expires_at = (
        effective_now
        + timedelta(
            seconds=lease_seconds
        )
    )

    page.crawl_claim_token = claim_token
    page.crawl_claimed_at = effective_now
    page.crawl_claim_expires_at = claim_expires_at

    db.flush()

    return CrawlClaim(
        monitored_page_id=page.id,
        organization_id=page.organization_id,
        url=page.url,
        crawl_frequency=page.crawl_frequency,
        claim_token=claim_token,
        claimed_at=effective_now,
        claim_expires_at=claim_expires_at,
    )


def _lock_claimed_page_for_finalization(
    db: Session,
    *,
    monitored_page_id: uuid.UUID,
    claim_token: uuid.UUID,
    effective_now: datetime,
) -> MonitoredPage:
    page = (
        db.query(MonitoredPage)
        .filter(
            MonitoredPage.id == monitored_page_id,
        )
        .with_for_update()
        .one_or_none()
    )

    if page is None:
        raise CrawlClaimAuthorityError(
            "Crawl claim no longer has finalization authority."
        )

    if (
        page.crawl_claim_token != claim_token
        or page.crawl_claim_expires_at is None
        or page.crawl_claim_expires_at <= effective_now
    ):
        raise CrawlClaimAuthorityError(
            "Crawl claim no longer has finalization authority."
        )

    return page


def _clear_crawl_claim(
    page: MonitoredPage,
) -> None:
    page.crawl_claim_token = None
    page.crawl_claimed_at = None
    page.crawl_claim_expires_at = None


def finalize_crawl_success(
    db: Session,
    *,
    monitored_page_id: uuid.UUID,
    claim_token: uuid.UUID,
) -> datetime:
    """
    Finalize a successful crawl while the claim still has authority.

    The caller owns commit / rollback.
    """
    effective_now = db.execute(
        select(
            func.clock_timestamp()
        )
    ).scalar_one()

    page = _lock_claimed_page_for_finalization(
        db,
        monitored_page_id=monitored_page_id,
        claim_token=claim_token,
        effective_now=effective_now,
    )

    interval = _CRAWL_FREQUENCY_INTERVALS.get(
        page.crawl_frequency
    )

    if interval is None:
        raise CrawlSchedulingConfigurationError(
            "Monitored page has unsupported crawl frequency."
        )

    next_crawl_at = (
        effective_now
        + interval
    )

    page.last_crawled_at = effective_now
    page.next_crawl_at = next_crawl_at
    page.last_crawl_attempt_at = effective_now
    page.last_crawl_status = "succeeded"
    page.last_crawl_failure_code = None
    page.consecutive_crawl_failures = 0

    _clear_crawl_claim(page)

    db.flush()

    return next_crawl_at


def finalize_crawl_failure(
    db: Session,
    *,
    monitored_page_id: uuid.UUID,
    claim_token: uuid.UUID,
    retry_delay_seconds: int = DEFAULT_CRAWL_FAILURE_RETRY_SECONDS,
    failure_code: str = "snapshot_unavailable",
) -> datetime:
    """
    Schedule a failed crawl for retry while the claim still has authority.

    Failure does not advance last_crawled_at. The caller owns commit /
    rollback.
    """
    if retry_delay_seconds < 1:
        raise CrawlSchedulingConfigurationError(
            "Crawl failure retry seconds must be at least 1."
        )
    if failure_code not in CRAWL_FAILURE_CODES:
        raise CrawlSchedulingConfigurationError("Unsupported crawl failure code.")

    effective_now = db.execute(
        select(
            func.clock_timestamp()
        )
    ).scalar_one()

    page = _lock_claimed_page_for_finalization(
        db,
        monitored_page_id=monitored_page_id,
        claim_token=claim_token,
        effective_now=effective_now,
    )

    next_crawl_at = (
        effective_now
        + timedelta(
            seconds=retry_delay_seconds
        )
    )

    page.next_crawl_at = next_crawl_at
    page.last_crawl_attempt_at = effective_now
    page.last_crawl_status = "failed"
    page.last_crawl_failure_code = failure_code
    page.consecutive_crawl_failures = (page.consecutive_crawl_failures or 0) + 1

    _clear_crawl_claim(page)

    db.flush()

    return next_crawl_at
