from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.core.database import SessionLocal
from src.models import Entity, MonitoredPage, Organization


RUN_DB_INTEGRATION_TESTS = (
    os.getenv(
        "RUN_DB_INTEGRATION_TESTS",
        "",
    )
    == "1"
)


pytestmark = pytest.mark.skipif(
    not RUN_DB_INTEGRATION_TESTS,
    reason=(
        "Set RUN_DB_INTEGRATION_TESTS=1 "
        "to run PostgreSQL integration tests."
    ),
)


def _create_organization_and_entity(
    db,
) -> tuple[Organization, Entity]:
    suffix = uuid.uuid4().hex[:10]

    organization = Organization(
        name=f"Crawl scheduling {suffix}",
        slug=f"crawl-scheduling-{suffix}",
        billing_email=(
            f"crawl-scheduling-{suffix}"
            "@example.test"
        ),
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
        organization_id=organization.id,
        name="OpenAI",
        domain=f"{suffix}.example.test",
        category="AI Technology",
    )

    db.add(
        entity
    )
    db.flush()

    return (
        organization,
        entity,
    )


def _monitored_page(
    *,
    organization_id,
    entity_id,
    url: str,
    crawl_frequency="daily",
    crawl_claim_token=None,
    crawl_claimed_at=None,
    crawl_claim_expires_at=None,
) -> MonitoredPage:
    return MonitoredPage(
        organization_id=organization_id,
        entity_id=entity_id,
        url=url,
        page_type="tos",
        crawl_frequency=crawl_frequency,
        is_active=True,
        crawl_claim_token=crawl_claim_token,
        crawl_claimed_at=crawl_claimed_at,
        crawl_claim_expires_at=crawl_claim_expires_at,
    )


def test_monitored_page_identity_is_unique_within_organization_entity():
    db = SessionLocal()

    try:
        (
            organization,
            entity,
        ) = _create_organization_and_entity(
            db
        )

        url = (
            "https://openai.com/"
            f"policies/{uuid.uuid4().hex}"
        )

        db.add(
            _monitored_page(
                organization_id=organization.id,
                entity_id=entity.id,
                url=url,
            )
        )
        db.flush()

        with pytest.raises(
            IntegrityError
        ):
            with db.begin_nested():
                db.add(
                    _monitored_page(
                        organization_id=organization.id,
                        entity_id=entity.id,
                        url=url,
                    )
                )
                db.flush()

        assert (
            db.query(
                MonitoredPage
            )
            .filter(
                MonitoredPage.organization_id
                == organization.id,
                MonitoredPage.entity_id
                == entity.id,
                MonitoredPage.url
                == url,
            )
            .count()
            == 1
        )

    finally:
        db.rollback()
        db.close()


def test_empty_crawl_claim_state_is_valid():
    db = SessionLocal()

    try:
        (
            organization,
            entity,
        ) = _create_organization_and_entity(
            db
        )

        page = _monitored_page(
            organization_id=organization.id,
            entity_id=entity.id,
            url=(
                "https://openai.com/"
                f"policies/{uuid.uuid4().hex}"
            ),
        )

        db.add(
            page
        )
        db.flush()

        assert page.crawl_claim_token is None
        assert page.crawl_claimed_at is None
        assert page.crawl_claim_expires_at is None

    finally:
        db.rollback()
        db.close()


def test_complete_crawl_claim_with_positive_lease_is_valid():
    db = SessionLocal()

    try:
        (
            organization,
            entity,
        ) = _create_organization_and_entity(
            db
        )

        claimed_at = datetime.now(
            timezone.utc
        )

        claim_token = uuid.uuid4()

        page = _monitored_page(
            organization_id=organization.id,
            entity_id=entity.id,
            url=(
                "https://openai.com/"
                f"policies/{uuid.uuid4().hex}"
            ),
            crawl_claim_token=claim_token,
            crawl_claimed_at=claimed_at,
            crawl_claim_expires_at=(
                claimed_at
                + timedelta(
                    minutes=10
                )
            ),
        )

        db.add(
            page
        )
        db.flush()

        assert (
            page.crawl_claim_token
            == claim_token
        )

    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize(
    (
        "token_present",
        "claimed_at_present",
        "expires_at_present",
    ),
    [
        (
            True,
            False,
            False,
        ),
        (
            False,
            True,
            False,
        ),
        (
            False,
            False,
            True,
        ),
        (
            True,
            True,
            False,
        ),
        (
            True,
            False,
            True,
        ),
        (
            False,
            True,
            True,
        ),
    ],
)
def test_partial_crawl_claim_state_is_rejected(
    token_present,
    claimed_at_present,
    expires_at_present,
):
    db = SessionLocal()

    try:
        (
            organization,
            entity,
        ) = _create_organization_and_entity(
            db
        )

        claimed_at = datetime.now(
            timezone.utc
        )

        page = _monitored_page(
            organization_id=organization.id,
            entity_id=entity.id,
            url=(
                "https://openai.com/"
                f"policies/{uuid.uuid4().hex}"
            ),
            crawl_claim_token=(
                uuid.uuid4()
                if token_present
                else None
            ),
            crawl_claimed_at=(
                claimed_at
                if claimed_at_present
                else None
            ),
            crawl_claim_expires_at=(
                claimed_at
                + timedelta(
                    minutes=10
                )
                if expires_at_present
                else None
            ),
        )

        with pytest.raises(
            IntegrityError
        ):
            with db.begin_nested():
                db.add(
                    page
                )
                db.flush()

    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize(
    "lease_delta",
    [
        timedelta(),
        timedelta(
            seconds=-1
        ),
    ],
)
def test_non_positive_crawl_claim_lease_is_rejected(
    lease_delta,
):
    db = SessionLocal()

    try:
        (
            organization,
            entity,
        ) = _create_organization_and_entity(
            db
        )

        claimed_at = datetime.now(
            timezone.utc
        )

        page = _monitored_page(
            organization_id=organization.id,
            entity_id=entity.id,
            url=(
                "https://openai.com/"
                f"policies/{uuid.uuid4().hex}"
            ),
            crawl_claim_token=uuid.uuid4(),
            crawl_claimed_at=claimed_at,
            crawl_claim_expires_at=(
                claimed_at
                + lease_delta
            ),
        )

        with pytest.raises(
            IntegrityError
        ):
            with db.begin_nested():
                db.add(
                    page
                )
                db.flush()

    finally:
        db.rollback()
        db.close()


def test_crawl_frequency_is_required():
    db = SessionLocal()

    try:
        (
            organization,
            entity,
        ) = _create_organization_and_entity(
            db
        )

        with pytest.raises(
            IntegrityError
        ):
            with db.begin_nested():
                db.execute(
                    text(
                        """
                        INSERT INTO monitored_pages (
                            id,
                            organization_id,
                            entity_id,
                            url,
                            page_type,
                            crawl_frequency,
                            is_active
                        )
                        VALUES (
                            :id,
                            :organization_id,
                            :entity_id,
                            :url,
                            'tos',
                            NULL,
                            TRUE
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "organization_id": organization.id,
                        "entity_id": entity.id,
                        "url": (
                            "https://openai.com/"
                            f"policies/{uuid.uuid4().hex}"
                        ),
                    },
                )

    finally:
        db.rollback()
        db.close()
