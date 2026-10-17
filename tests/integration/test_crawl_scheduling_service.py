from __future__ import annotations

from datetime import timedelta
import os
import threading
import uuid

import pytest
from sqlalchemy import delete, func, select

from src.core.database import SessionLocal
from src.models import Entity, MonitoredPage, Organization
from src.services.crawl_scheduling_service import (
    CrawlSchedulingConfigurationError,
    claim_due_monitored_page,
    CrawlClaimAuthorityError,
    finalize_crawl_failure,
    finalize_crawl_success,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(
    db,
) -> Organization:
    suffix = uuid.uuid4().hex[:12]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"crawl-scheduling-{suffix}",
        slug=f"crawl-scheduling-{suffix}",
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

    return organization


def _make_entity(
    db,
    *,
    organization_id: uuid.UUID,
) -> Entity:
    suffix = uuid.uuid4().hex[:12]

    entity = Entity(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name=f"Crawl source {suffix}",
        domain=f"{suffix}.example.test",
        category="Technology",
    )

    db.add(
        entity
    )
    db.flush()

    return entity


def _make_page(
    db,
    *,
    organization_id: uuid.UUID,
    entity_id: uuid.UUID,
    is_active: bool = True,
    crawl_frequency="daily",
    last_crawled_at=None,
    next_crawl_at=None,
    crawl_claim_token=None,
    crawl_claimed_at=None,
    crawl_claim_expires_at=None,
) -> MonitoredPage:
    page = MonitoredPage(
        id=uuid.uuid4(),
        organization_id=organization_id,
        entity_id=entity_id,
        url=(
            "https://openai.com/policies/"
            f"{uuid.uuid4().hex}"
        ),
        page_type="tos",
        crawl_frequency=crawl_frequency,
        is_active=is_active,
        last_crawled_at=last_crawled_at,
        next_crawl_at=next_crawl_at,
        crawl_claim_token=crawl_claim_token,
        crawl_claimed_at=crawl_claimed_at,
        crawl_claim_expires_at=crawl_claim_expires_at,
    )

    db.add(
        page
    )
    db.flush()

    return page


def _cleanup(
    organization_ids: list[uuid.UUID],
) -> None:
    if not organization_ids:
        return

    db = SessionLocal()

    try:
        db.rollback()

        db.execute(
            delete(
                MonitoredPage
            ).where(
                MonitoredPage.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(
                Entity
            ).where(
                Entity.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(
                Organization
            ).where(
                Organization.id.in_(
                    organization_ids
                )
            )
        )

        db.commit()

    finally:
        db.close()


def test_claim_rejects_invalid_lease_seconds() -> None:
    db = SessionLocal()

    try:
        with pytest.raises(
            CrawlSchedulingConfigurationError
        ):
            claim_due_monitored_page(
                db,
                lease_seconds=0,
            )

    finally:
        db.rollback()
        db.close()


def test_claim_returns_none_when_no_page_is_due() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        entity = _make_entity(
            db,
            organization_id=organization.id,
        )

        now = db.execute(
            select(
                func.clock_timestamp()
            )
        ).scalar_one()

        _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
            next_crawl_at=(
                now
                + timedelta(
                    hours=1
                )
            ),
        )

        db.commit()

        assert (
            claim_due_monitored_page(
                db,
                organization_id=organization.id,
            )
            is None
        )

    finally:
        db.rollback()
        db.close()
        _cleanup(
            organization_ids
        )


def test_claim_skips_inactive_page() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        entity = _make_entity(
            db,
            organization_id=organization.id,
        )

        _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
            is_active=False,
        )

        db.commit()

        assert (
            claim_due_monitored_page(
                db,
                organization_id=organization.id,
            )
            is None
        )

    finally:
        db.rollback()
        db.close()
        _cleanup(
            organization_ids
        )


def test_first_claim_persists_lease_after_commit() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        entity = _make_entity(
            db,
            organization_id=organization.id,
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
        )

        page_id = page.id
        organization_id = organization.id
        url = page.url

        db.commit()

        before_claim = db.execute(
            select(
                func.clock_timestamp()
            )
        ).scalar_one()

        claim = claim_due_monitored_page(
            db,
            organization_id=organization_id,
            lease_seconds=60,
        )

        assert claim is not None
        assert claim.monitored_page_id == page_id
        assert claim.organization_id == organization_id
        assert claim.url == url
        assert claim.crawl_frequency == "daily"
        assert claim.claim_expires_at > claim.claimed_at
        assert claim.claimed_at >= before_claim

        db.commit()

        db.expire_all()

        persisted = db.get(
            MonitoredPage,
            page_id,
        )

        assert persisted is not None
        assert (
            persisted.crawl_claim_token
            == claim.claim_token
        )
        assert (
            persisted.crawl_claimed_at
            == claim.claimed_at
        )
        assert (
            persisted.crawl_claim_expires_at
            == claim.claim_expires_at
        )

    finally:
        db.rollback()
        db.close()
        _cleanup(
            organization_ids
        )


def test_unexpired_claim_is_not_claimable() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        entity = _make_entity(
            db,
            organization_id=organization.id,
        )

        now = db.execute(
            select(
                func.clock_timestamp()
            )
        ).scalar_one()

        _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
            crawl_claim_token=uuid.uuid4(),
            crawl_claimed_at=now,
            crawl_claim_expires_at=(
                now
                + timedelta(
                    minutes=5
                )
            ),
        )

        db.commit()

        assert (
            claim_due_monitored_page(
                db,
                organization_id=organization.id,
            )
            is None
        )

    finally:
        db.rollback()
        db.close()
        _cleanup(
            organization_ids
        )


def test_expired_claim_is_reclaimed_with_new_token() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        entity = _make_entity(
            db,
            organization_id=organization.id,
        )

        now = db.execute(
            select(
                func.clock_timestamp()
            )
        ).scalar_one()

        old_token = uuid.uuid4()

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
            crawl_claim_token=old_token,
            crawl_claimed_at=(
                now
                - timedelta(
                    minutes=10
                )
            ),
            crawl_claim_expires_at=(
                now
                - timedelta(
                    minutes=5
                )
            ),
        )

        page_id = page.id

        db.commit()

        claim = claim_due_monitored_page(
            db,
            organization_id=organization.id,
            lease_seconds=60,
        )

        assert claim is not None
        assert claim.monitored_page_id == page_id
        assert claim.claim_token != old_token

        db.commit()

        db.expire_all()

        persisted = db.get(
            MonitoredPage,
            page_id,
        )

        assert persisted is not None
        assert (
            persisted.crawl_claim_token
            == claim.claim_token
        )

    finally:
        db.rollback()
        db.close()
        _cleanup(
            organization_ids
        )


def test_claim_without_organization_scope_remains_global() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        entity = _make_entity(
            db,
            organization_id=organization.id,
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
        )

        page_id = page.id

        db.commit()

        claim = claim_due_monitored_page(
            db,
            lease_seconds=60,
        )

        assert claim is not None

        db.rollback()

        scoped_claim = claim_due_monitored_page(
            db,
            organization_id=organization.id,
            lease_seconds=60,
        )

        assert scoped_claim is not None
        assert scoped_claim.monitored_page_id == page_id

    finally:
        db.rollback()
        db.close()
        _cleanup(organization_ids)


def test_concurrent_claimers_do_not_claim_same_page() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            setup_db
        )
        organization_ids.append(
            organization.id
        )

        entity = _make_entity(
            setup_db,
            organization_id=organization.id,
        )

        page = _make_page(
            setup_db,
            organization_id=organization.id,
            entity_id=entity.id,
        )

        page_id = page.id
        organization_id = organization.id

        setup_db.commit()

        barrier = threading.Barrier(
            2
        )
        results = []
        errors = []
        results_lock = threading.Lock()

        def worker() -> None:
            db = SessionLocal()

            try:
                barrier.wait(
                    timeout=5
                )

                claim = claim_due_monitored_page(
                    db,
                    organization_id=organization_id,
                    lease_seconds=60,
                )

                db.commit()

                with results_lock:
                    results.append(
                        claim
                    )

            except BaseException as exc:
                db.rollback()

                with results_lock:
                    errors.append(
                        exc
                    )

            finally:
                db.close()

        threads = [
            threading.Thread(
                target=worker
            )
            for _ in range(
                2
            )
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(
                timeout=10
            )

        assert all(
            not thread.is_alive()
            for thread in threads
        )
        assert errors == []

        claims = [
            result
            for result in results
            if result is not None
        ]

        assert len(results) == 2
        assert len(claims) == 1
        assert (
            claims[0].monitored_page_id
            == page_id
        )

    finally:
        setup_db.rollback()
        setup_db.close()
        _cleanup(
            organization_ids
        )

@pytest.mark.parametrize(
    ("crawl_frequency", "expected_delta"),
    [
        ("hourly", timedelta(hours=1)),
        ("daily", timedelta(days=1)),
        ("weekly", timedelta(weeks=1)),
    ],
)
def test_finalize_success_schedules_next_frequency_and_clears_claim(
    crawl_frequency: str,
    expected_delta: timedelta,
) -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        entity = _make_entity(
            db,
            organization_id=organization.id,
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
            crawl_frequency=crawl_frequency,
        )

        page_id = page.id
        organization_id = organization.id

        db.commit()

        claim = claim_due_monitored_page(
            db,
            organization_id=organization_id,
            lease_seconds=60,
        )

        assert claim is not None

        db.commit()

        before_finalize = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        next_crawl_at = finalize_crawl_success(
            db,
            monitored_page_id=page_id,
            claim_token=claim.claim_token,
        )

        db.commit()

        after_finalize = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        db.expire_all()

        persisted = db.get(
            MonitoredPage,
            page_id,
        )

        assert persisted is not None
        assert persisted.last_crawled_at is not None
        assert (
            before_finalize
            <= persisted.last_crawled_at
            <= after_finalize
        )
        assert persisted.next_crawl_at == next_crawl_at
        assert (
            persisted.next_crawl_at
            - persisted.last_crawled_at
            == expected_delta
        )
        assert persisted.crawl_claim_token is None
        assert persisted.crawl_claimed_at is None
        assert persisted.crawl_claim_expires_at is None

    finally:
        db.rollback()
        db.close()
        _cleanup(organization_ids)


def test_finalize_failure_schedules_retry_without_advancing_last_crawled_at() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        entity = _make_entity(
            db,
            organization_id=organization.id,
        )

        previous_success = db.execute(
            select(func.clock_timestamp())
        ).scalar_one() - timedelta(hours=2)

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
            last_crawled_at=previous_success,
        )

        page_id = page.id
        organization_id = organization.id

        db.commit()

        claim = claim_due_monitored_page(
            db,
            organization_id=organization_id,
            lease_seconds=60,
        )

        assert claim is not None

        db.commit()

        before_finalize = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        next_crawl_at = finalize_crawl_failure(
            db,
            monitored_page_id=page_id,
            claim_token=claim.claim_token,
            retry_delay_seconds=120,
        )

        db.commit()

        after_finalize = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        db.expire_all()

        persisted = db.get(
            MonitoredPage,
            page_id,
        )

        assert persisted is not None
        assert persisted.last_crawled_at == previous_success
        assert persisted.next_crawl_at == next_crawl_at
        assert (
            before_finalize + timedelta(seconds=120)
            <= persisted.next_crawl_at
            <= after_finalize + timedelta(seconds=120)
        )
        assert persisted.crawl_claim_token is None
        assert persisted.crawl_claimed_at is None
        assert persisted.crawl_claim_expires_at is None

    finally:
        db.rollback()
        db.close()
        _cleanup(organization_ids)


def test_finalize_rejects_wrong_claim_token_without_mutation() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        entity = _make_entity(
            db,
            organization_id=organization.id,
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
        )

        page_id = page.id
        organization_id = organization.id

        db.commit()

        claim = claim_due_monitored_page(
            db,
            organization_id=organization_id,
            lease_seconds=60,
        )

        assert claim is not None

        db.commit()

        with pytest.raises(
            CrawlClaimAuthorityError
        ):
            finalize_crawl_success(
                db,
                monitored_page_id=page_id,
                claim_token=uuid.uuid4(),
            )

        db.rollback()
        db.expire_all()

        persisted = db.get(
            MonitoredPage,
            page_id,
        )

        assert persisted is not None
        assert persisted.last_crawled_at is None
        assert persisted.next_crawl_at is None
        assert persisted.crawl_claim_token == claim.claim_token

    finally:
        db.rollback()
        db.close()
        _cleanup(organization_ids)


def test_finalize_rejects_expired_matching_claim() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        entity = _make_entity(
            db,
            organization_id=organization.id,
        )

        now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        claim_token = uuid.uuid4()

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
            crawl_claim_token=claim_token,
            crawl_claimed_at=now - timedelta(minutes=2),
            crawl_claim_expires_at=now - timedelta(minutes=1),
        )

        page_id = page.id

        db.commit()

        with pytest.raises(
            CrawlClaimAuthorityError
        ):
            finalize_crawl_success(
                db,
                monitored_page_id=page_id,
                claim_token=claim_token,
            )

        db.rollback()
        db.expire_all()

        persisted = db.get(
            MonitoredPage,
            page_id,
        )

        assert persisted is not None
        assert persisted.last_crawled_at is None
        assert persisted.next_crawl_at is None
        assert persisted.crawl_claim_token == claim_token

    finally:
        db.rollback()
        db.close()
        _cleanup(organization_ids)


def test_reclaimed_claim_fences_old_holder_from_finalization() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        entity = _make_entity(
            db,
            organization_id=organization.id,
        )

        now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        old_token = uuid.uuid4()

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
            crawl_claim_token=old_token,
            crawl_claimed_at=now - timedelta(minutes=2),
            crawl_claim_expires_at=now - timedelta(minutes=1),
        )

        page_id = page.id
        organization_id = organization.id

        db.commit()

        replacement_claim = claim_due_monitored_page(
            db,
            organization_id=organization_id,
            lease_seconds=60,
        )

        assert replacement_claim is not None
        assert replacement_claim.claim_token != old_token

        db.commit()

        with pytest.raises(
            CrawlClaimAuthorityError
        ):
            finalize_crawl_success(
                db,
                monitored_page_id=page_id,
                claim_token=old_token,
            )

        db.rollback()
        db.expire_all()

        persisted = db.get(
            MonitoredPage,
            page_id,
        )

        assert persisted is not None
        assert (
            persisted.crawl_claim_token
            == replacement_claim.claim_token
        )
        assert persisted.last_crawled_at is None

    finally:
        db.rollback()
        db.close()
        _cleanup(organization_ids)


def test_finalize_failure_rejects_invalid_retry_delay() -> None:
    db = SessionLocal()

    try:
        with pytest.raises(
            CrawlSchedulingConfigurationError
        ):
            finalize_crawl_failure(
                db,
                monitored_page_id=uuid.uuid4(),
                claim_token=uuid.uuid4(),
                retry_delay_seconds=0,
            )

    finally:
        db.rollback()
        db.close()