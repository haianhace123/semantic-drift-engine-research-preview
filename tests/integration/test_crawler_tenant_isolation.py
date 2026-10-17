from __future__ import annotations

import hashlib
import os
import uuid

import pytest

from src.core.database import SessionLocal
from src.crawler import persistence
from src.models import (
    Entity,
    MonitoredPage,
    Organization,
    PageSnapshot,
)


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


def _organization(
    *,
    suffix: str,
) -> Organization:
    return Organization(
        name=f"Crawler isolation {suffix}",
        slug=(
            "crawler-isolation-"
            f"{suffix}-"
            f"{uuid.uuid4().hex[:10]}"
        ),
        billing_email=(
            f"crawler-{suffix}-"
            f"{uuid.uuid4().hex[:8]}"
            "@example.test"
        ),
        subscription_tier="free",
        quota_urls=10,
        quota_requests=1000,
        is_active=True,
    )


def _sha256(
    content: str,
) -> str:
    return hashlib.sha256(
        content.encode(
            "utf-8"
        )
    ).hexdigest()


def test_same_domain_is_isolated_between_organizations(
    monkeypatch,
    tmp_path,
):
    """
    The same external entity/domain must be independently representable
    by different tenants.

    This is the critical ingestion isolation invariant: a crawler for
    tenant B must never reuse tenant A's Entity or MonitoredPage.
    """
    monkeypatch.setattr(
        persistence.Paths,
        "get_data_dir",
        lambda: tmp_path,
    )

    db = SessionLocal()

    organization_a = _organization(
        suffix="a"
    )

    organization_b = _organization(
        suffix="b"
    )

    try:
        db.add_all(
            [
                organization_a,
                organization_b,
            ]
        )

        db.commit()

        db.refresh(
            organization_a
        )

        db.refresh(
            organization_b
        )

        content_a = (
            "Tenant A version of the Google privacy policy. "
            "The semantic state belongs exclusively to organization A."
        )

        content_b = (
            "Tenant B version of the Google privacy policy. "
            "The semantic state belongs exclusively to organization B."
        )

        snapshot_file_a = (
            persistence.persist_snapshot_content(
                source_name="google",
                page_type="privacy",
                content=content_a,
            )
        )

        snapshot_file_b = (
            persistence.persist_snapshot_content(
                source_name="google",
                page_type="privacy",
                content=content_b,
            )
        )

        snapshot_a = (
            persistence.create_snapshot_record(
                db=db,
                organization_id=organization_a.id,
                entity_name="Google",
                entity_domain="google.com",
                entity_category="Technology",
                url="https://policies.google.com/privacy",
                page_type="privacy",
                content_hash=_sha256(
                    content_a
                ),
                snapshot_file=snapshot_file_a,
            )
        )

        snapshot_b = (
            persistence.create_snapshot_record(
                db=db,
                organization_id=organization_b.id,
                entity_name="Google",
                entity_domain="google.com",
                entity_category="Technology",
                url="https://policies.google.com/privacy",
                page_type="privacy",
                content_hash=_sha256(
                    content_b
                ),
                snapshot_file=snapshot_file_b,
            )
        )

        entity_a = (
            db.query(
                Entity
            )
            .filter(
                Entity.organization_id
                == organization_a.id,
                Entity.domain
                == "google.com",
            )
            .one()
        )

        entity_b = (
            db.query(
                Entity
            )
            .filter(
                Entity.organization_id
                == organization_b.id,
                Entity.domain
                == "google.com",
            )
            .one()
        )

        page_a = (
            db.query(
                MonitoredPage
            )
            .filter(
                MonitoredPage.organization_id
                == organization_a.id,
                MonitoredPage.entity_id
                == entity_a.id,
            )
            .one()
        )

        page_b = (
            db.query(
                MonitoredPage
            )
            .filter(
                MonitoredPage.organization_id
                == organization_b.id,
                MonitoredPage.entity_id
                == entity_b.id,
            )
            .one()
        )

        persisted_snapshot_a = (
            db.query(
                PageSnapshot
            )
            .filter(
                PageSnapshot.id
                == snapshot_a.id
            )
            .one()
        )

        persisted_snapshot_b = (
            db.query(
                PageSnapshot
            )
            .filter(
                PageSnapshot.id
                == snapshot_b.id
            )
            .one()
        )

        # ------------------------------------------------------------
        # Tenant isolation
        # ------------------------------------------------------------

        assert (
            entity_a.id
            != entity_b.id
        )

        assert (
            entity_a.organization_id
            == organization_a.id
        )

        assert (
            entity_b.organization_id
            == organization_b.id
        )

        assert (
            page_a.id
            != page_b.id
        )

        assert (
            page_a.organization_id
            == organization_a.id
        )

        assert (
            page_b.organization_id
            == organization_b.id
        )

        assert (
            persisted_snapshot_a.organization_id
            == organization_a.id
        )

        assert (
            persisted_snapshot_b.organization_id
            == organization_b.id
        )

        # ------------------------------------------------------------
        # Entity metadata
        # ------------------------------------------------------------

        assert (
            entity_a.category
            == "Technology"
        )

        assert (
            entity_b.category
            == "Technology"
        )

        # ------------------------------------------------------------
        # Snapshot integrity
        # ------------------------------------------------------------

        assert (
            persisted_snapshot_a.content_hash
            == _sha256(
                content_a
            )
        )

        assert (
            persisted_snapshot_b.content_hash
            == _sha256(
                content_b
            )
        )

        assert (
            persisted_snapshot_a.content_size
            == len(
                content_a.encode(
                    "utf-8"
                )
            )
        )

        assert (
            persisted_snapshot_b.content_size
            == len(
                content_b.encode(
                    "utf-8"
                )
            )
        )

        assert (
            persisted_snapshot_a.content_size
            != 64
        )

        assert (
            persisted_snapshot_b.content_size
            != 64
        )

        assert (
            snapshot_file_a.path
            != snapshot_file_b.path
        )

        with open(
            snapshot_file_a.path,
            "r",
            encoding="utf-8",
        ) as file_a:
            assert (
                file_a.read()
                == content_a
            )

        with open(
            snapshot_file_b.path,
            "r",
            encoding="utf-8",
        ) as file_b:
            assert (
                file_b.read()
                == content_b
            )

    finally:
        db.rollback()

        organization_ids = [
            organization_id
            for organization_id in (
                organization_a.id,
                organization_b.id,
            )
            if organization_id
            is not None
        ]

        if organization_ids:
            (
                db.query(
                    Organization
                )
                .filter(
                    Organization.id.in_(
                        organization_ids
                    )
                )
                .delete(
                    synchronize_session=False
                )
            )

            db.commit()

        db.close()