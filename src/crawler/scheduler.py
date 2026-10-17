from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import threading
import time
import uuid

from src.core.database import SessionLocal
from src.crawler.public_html_crawler import PublicHTMLCrawlFailure
from src.crawler.registry import (
    UnsupportedCrawlerSourceError,
    crawl_monitored_page,
)
from src.services.crawl_scheduling_service import (
    CrawlClaim,
    CrawlClaimAuthorityError,
    finalize_crawl_failure,
    finalize_crawl_success,
    claim_due_monitored_page,
)
from src.services.worker_operational_reporter import (
    WorkerOperationalReporter,
)


logger = logging.getLogger(__name__)


class CrawlCycleStatus(str, Enum):
    IDLE = "idle"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CrawlCycleResult:
    status: CrawlCycleStatus
    claim: CrawlClaim | None = None


class CrawlSchedulerConfigurationError(
    ValueError
):
    pass


class CrawlScheduler:
    def __init__(
        self,
        *,
        poll_seconds: float,
        operational_reporter: WorkerOperationalReporter | None = None,
    ) -> None:
        if poll_seconds <= 0:
            raise CrawlSchedulerConfigurationError(
                "poll_seconds must be greater than zero"
            )

        self.poll_seconds = poll_seconds
        self.operational_reporter = operational_reporter
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        while not self.stop_event.is_set():
            if self.operational_reporter is not None:
                self.operational_reporter.cycle_started()

            started_at = time.perf_counter()

            try:
                result = run_crawl_cycle()
            except Exception as exc:
                if self.operational_reporter is not None:
                    self.operational_reporter.cycle_failed(exc)
                raise

            if self.operational_reporter is not None:
                if result.status == CrawlCycleStatus.FAILED:
                    self.operational_reporter.cycle_failed(
                        "crawler_page_crawl_failed"
                    )
                else:
                    self.operational_reporter.cycle_succeeded()

            duration_ms = round(
                (time.perf_counter() - started_at) * 1000,
                3,
            )

            if result.status == CrawlCycleStatus.SUCCEEDED:
                logger.info(
                    "Crawler cycle completed: "
                    "status=%s duration_ms=%s "
                    "organization_id=%s monitored_page_id=%s.",
                    result.status.value,
                    duration_ms,
                    (
                        result.claim.organization_id
                        if result.claim is not None
                        else None
                    ),
                    (
                        result.claim.monitored_page_id
                        if result.claim is not None
                        else None
                    ),
                )
            elif result.status == CrawlCycleStatus.FAILED:
                logger.warning(
                    "Crawler cycle completed: "
                    "status=%s duration_ms=%s "
                    "organization_id=%s monitored_page_id=%s.",
                    result.status.value,
                    duration_ms,
                    (
                        result.claim.organization_id
                        if result.claim is not None
                        else None
                    ),
                    (
                        result.claim.monitored_page_id
                        if result.claim is not None
                        else None
                    ),
                )

            if self.stop_event.is_set():
                break

            if result.status == CrawlCycleStatus.IDLE:
                self.stop_event.wait(
                    self.poll_seconds
                )


def _claim_next_page(
    *,
    organization_id: uuid.UUID | None = None,
) -> CrawlClaim | None:
    db = SessionLocal()

    try:
        claim = claim_due_monitored_page(
            db,
            organization_id=organization_id,
        )

        db.commit()

        return claim

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def _finalize_success(
    claim: CrawlClaim,
) -> None:
    db = SessionLocal()

    try:
        finalize_crawl_success(
            db,
            monitored_page_id=claim.monitored_page_id,
            claim_token=claim.claim_token,
        )

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def _finalize_failure(
    claim: CrawlClaim,
    *,
    failure_code: str = "snapshot_unavailable",
) -> None:
    db = SessionLocal()

    try:
        finalize_crawl_failure(
            db,
            monitored_page_id=claim.monitored_page_id,
            claim_token=claim.claim_token,
            failure_code=failure_code,
        )

        db.commit()

    except CrawlClaimAuthorityError:
        db.rollback()
        logger.info(
            "Crawl claim superseded before failure finalization: page=%s",
            claim.monitored_page_id,
        )
    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def run_crawl_cycle(
    *,
    organization_id: uuid.UUID | None = None,
) -> CrawlCycleResult:
    """
    Claim and execute at most one due monitored page.

    The durable claim is committed before crawler execution. External crawl
    work runs without a scheduler-owned database session. Finalization uses
    a fresh transaction fenced by the claim token.
    """
    claim = _claim_next_page(
        organization_id=organization_id,
    )

    if claim is None:
        return CrawlCycleResult(
            status=CrawlCycleStatus.IDLE,
        )

    try:
        snapshot = crawl_monitored_page(
            url=claim.url,
            organization_id=claim.organization_id,
            monitored_page_id=claim.monitored_page_id,
            claim_token=claim.claim_token,
        )

    except PublicHTMLCrawlFailure as exc:
        _finalize_failure(claim, failure_code=exc.code)
        return CrawlCycleResult(status=CrawlCycleStatus.FAILED, claim=claim)

    except UnsupportedCrawlerSourceError as exc:
        _finalize_failure(claim, failure_code="unsupported_source")

        logger.warning(
            "Crawler source unsupported: organization_id=%s "
            "monitored_page_id=%s reason=%s.",
            claim.organization_id,
            claim.monitored_page_id,
            exc,
        )

        return CrawlCycleResult(
            status=CrawlCycleStatus.FAILED,
            claim=claim,
        )

    except Exception:
        _finalize_failure(claim, failure_code="internal_error")

        return CrawlCycleResult(
            status=CrawlCycleStatus.FAILED,
            claim=claim,
        )

    if snapshot is None:
        _finalize_failure(
            claim
        )

        return CrawlCycleResult(
            status=CrawlCycleStatus.FAILED,
            claim=claim,
        )

    try:
        _finalize_success(claim)
    except CrawlClaimAuthorityError:
        # A later worker now owns the row. Do not report success for the
        # superseded claim, and do not crash the scheduler on lease loss.
        logger.info(
            "Crawl claim superseded before success finalization: page=%s",
            claim.monitored_page_id,
        )
        return CrawlCycleResult(
            status=CrawlCycleStatus.FAILED,
            claim=claim,
        )

    return CrawlCycleResult(
        status=CrawlCycleStatus.SUCCEEDED,
        claim=claim,
    )