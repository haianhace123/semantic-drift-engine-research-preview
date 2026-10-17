from __future__ import annotations

from datetime import timedelta
import os
import uuid

import pytest
from sqlalchemy import delete, func, select

from src.core.database import SessionLocal
from src.crawler import scheduler
from src.crawler.scheduler import CrawlCycleStatus
from src.models import Entity, MonitoredPage, Organization


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _create_due_page() -> tuple[
    uuid.UUID,
    uuid.UUID,
    str,
]:
    db = SessionLocal()

    try:
        suffix = uuid.uuid4().hex[:12]

        organization = Organization(
            id=uuid.uuid4(),
            name=f"crawler-scheduler-{suffix}",
            slug=f"crawler-scheduler-{suffix}",
            billing_email=f"{suffix}@example.test",
            subscription_tier="free",
            quota_urls=10,
            quota_requests=1000,
            is_active=True,
        )

        db.add(
            organization
        )

        db.flush()

        entity = Entity(
            id=uuid.uuid4(),
            organization_id=organization.id,
            name=f"Scheduler source {suffix}",
            domain=f"{suffix}.example.test",
            category="Technology",
        )

        db.add(
            entity
        )

        db.flush()

        now = db.execute(
            select(
                func.clock_timestamp()
            )
        ).scalar_one()

        page = MonitoredPage(
            id=uuid.uuid4(),
            organization_id=organization.id,
            entity_id=entity.id,
            url=(
                "https://openai.com/policies/"
                f"{uuid.uuid4().hex}"
            ),
            page_type="tos",
            crawl_frequency="daily",
            is_active=True,
            next_crawl_at=(
                now
                - timedelta(
                    days=3650
                )
            ),
        )

        db.add(
            page
        )

        db.commit()

        return (
            organization.id,
            page.id,
            page.url,
        )

    finally:
        db.close()


def _cleanup(
    organization_id: uuid.UUID,
) -> None:
    db = SessionLocal()

    try:
        db.rollback()

        db.execute(
            delete(
                MonitoredPage
            ).where(
                MonitoredPage.organization_id
                == organization_id
            )
        )

        db.execute(
            delete(
                Entity
            ).where(
                Entity.organization_id
                == organization_id
            )
        )

        db.execute(
            delete(
                Organization
            ).where(
                Organization.id
                == organization_id
            )
        )

        db.commit()

    finally:
        db.close()


def test_success_cycle_commits_claim_before_crawl_and_finalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        organization_id,
        page_id,
        url,
    ) = _create_due_page()

    observed_claim_token: uuid.UUID | None = None

    def fake_crawl(
        *,
        url: str,
        organization_id: uuid.UUID,
        monitored_page_id: uuid.UUID,
        claim_token: uuid.UUID,
    ):
        nonlocal observed_claim_token

        assert organization_id == (
            expected_organization_id
        )

        assert monitored_page_id == (
            page_id
        )

        assert url == expected_url

        verification_db = SessionLocal()

        try:
            persisted = verification_db.get(
                MonitoredPage,
                page_id,
            )

            assert persisted is not None
            assert (
                persisted.crawl_claim_token
                is not None
            )
            assert claim_token == persisted.crawl_claim_token
            assert (
                persisted.crawl_claimed_at
                is not None
            )
            assert (
                persisted.crawl_claim_expires_at
                is not None
            )

            observed_claim_token = (
                persisted.crawl_claim_token
            )

        finally:
            verification_db.close()

        return object()

    expected_organization_id = (
        organization_id
    )
    expected_url = url

    monkeypatch.setattr(
        scheduler,
        "crawl_monitored_page",
        fake_crawl,
    )

    try:
        result = scheduler.run_crawl_cycle(
            organization_id=organization_id,
        )

        assert (
            result.status
            == CrawlCycleStatus.SUCCEEDED
        )

        assert result.claim is not None
        assert (
            result.claim.monitored_page_id
            == page_id
        )
        assert (
            result.claim.organization_id
            == organization_id
        )
        assert (
            result.claim.url
            == url
        )
        assert (
            observed_claim_token
            == result.claim.claim_token
        )

        verification_db = SessionLocal()

        try:
            persisted = verification_db.get(
                MonitoredPage,
                page_id,
            )

            assert persisted is not None
            assert (
                persisted.last_crawled_at
                is not None
            )
            assert (
                persisted.next_crawl_at
                is not None
            )
            assert (
                persisted.next_crawl_at
                > persisted.last_crawled_at
            )
            assert (
                persisted.crawl_claim_token
                is None
            )
            assert (
                persisted.crawl_claimed_at
                is None
            )
            assert (
                persisted.crawl_claim_expires_at
                is None
            )

        finally:
            verification_db.close()

    finally:
        _cleanup(
            organization_id
        )


def test_none_snapshot_persists_failure_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        organization_id,
        page_id,
        _,
    ) = _create_due_page()

    monkeypatch.setattr(
        scheduler,
        "crawl_monitored_page",
        lambda **kwargs: None,
    )

    try:
        result = scheduler.run_crawl_cycle(
            organization_id=organization_id,
        )

        assert (
            result.status
            == CrawlCycleStatus.FAILED
        )

        assert result.claim is not None
        assert (
            result.claim.monitored_page_id
            == page_id
        )

        verification_db = SessionLocal()

        try:
            persisted = verification_db.get(
                MonitoredPage,
                page_id,
            )

            assert persisted is not None
            assert (
                persisted.last_crawled_at
                is None
            )
            assert (
                persisted.next_crawl_at
                is not None
            )
            assert (
                persisted.crawl_claim_token
                is None
            )
            assert (
                persisted.crawl_claimed_at
                is None
            )
            assert (
                persisted.crawl_claim_expires_at
                is None
            )

        finally:
            verification_db.close()

    finally:
        _cleanup(
            organization_id
        )


def test_crawler_exception_persists_failure_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        organization_id,
        page_id,
        _,
    ) = _create_due_page()

    def fail_crawl(
        **kwargs,
    ):
        raise RuntimeError(
            "crawler failed"
        )

    monkeypatch.setattr(
        scheduler,
        "crawl_monitored_page",
        fail_crawl,
    )

    try:
        result = scheduler.run_crawl_cycle(
            organization_id=organization_id,
        )

        assert (
            result.status
            == CrawlCycleStatus.FAILED
        )

        assert result.claim is not None
        assert (
            result.claim.monitored_page_id
            == page_id
        )

        verification_db = SessionLocal()

        try:
            persisted = verification_db.get(
                MonitoredPage,
                page_id,
            )

            assert persisted is not None
            assert (
                persisted.last_crawled_at
                is None
            )
            assert (
                persisted.next_crawl_at
                is not None
            )
            assert (
                persisted.crawl_claim_token
                is None
            )
            assert (
                persisted.crawl_claimed_at
                is None
            )
            assert (
                persisted.crawl_claim_expires_at
                is None
            )

        finally:
            verification_db.close()

    finally:
        _cleanup(
            organization_id
        )
