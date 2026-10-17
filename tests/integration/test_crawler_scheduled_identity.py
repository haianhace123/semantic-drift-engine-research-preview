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


URL = "https://policies.google.com/privacy"


def _organization(
    *,
    suffix: str,
) -> Organization:
    return Organization(
        name=f"Crawler scheduled identity {suffix}",
        slug=(
            "crawler-scheduled-identity-"
            f"{suffix}-"
            f"{uuid.uuid4().hex[:10]}"
        ),
        billing_email=(
            f"crawler-scheduled-{suffix}-"
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


def _create_page(
    db,
    *,
    organization: Organization,
    url: str = URL,
    page_type: str = "privacy",
) -> MonitoredPage:
    entity = Entity(
        organization_id=organization.id,
        name="Google",
        domain=(
            "google.com-"
            f"{uuid.uuid4().hex}"
        ),
        category="Technology",
    )

    db.add(
        entity
    )

    db.flush()

    page = MonitoredPage(
        organization_id=organization.id,
        entity_id=entity.id,
        url=url,
        page_type=page_type,
    )

    db.add(
        page
    )

    db.commit()

    db.refresh(
        page
    )

    return page


def _snapshot_file(
    monkeypatch,
    tmp_path,
    *,
    content: str,
) -> persistence.SnapshotFile:
    monkeypatch.setattr(
        persistence.Paths,
        "get_data_dir",
        lambda: tmp_path,
    )

    return persistence.persist_snapshot_content(
        source_name="google",
        page_type="privacy",
        content=content,
    )


def _cleanup(
    db,
    *,
    organization_ids: list[uuid.UUID],
) -> None:
    db.rollback()

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


def test_scheduled_snapshot_binds_to_exact_monitored_page(
    monkeypatch,
    tmp_path,
):
    db = SessionLocal()

    organization = _organization(
        suffix="exact"
    )

    try:
        db.add(
            organization
        )
        db.commit()
        db.refresh(
            organization
        )

        page = _create_page(
            db,
            organization=organization,
        )

        content = (
            "Scheduled crawl must persist against "
            "the exact claimed monitored page."
        )

        snapshot_file = _snapshot_file(
            monkeypatch,
            tmp_path,
            content=content,
        )

        snapshot = persistence.create_snapshot_record(
            db=db,
            organization_id=organization.id,
            entity_name="Ignored scheduled metadata",
            entity_domain="ignored.example",
            entity_category=None,
            url=page.url,
            page_type=page.page_type,
            content_hash=_sha256(
                content
            ),
            snapshot_file=snapshot_file,
            monitored_page_id=page.id,
        )

        persisted_snapshot = (
            db.query(
                PageSnapshot
            )
            .filter(
                PageSnapshot.id
                == snapshot.id
            )
            .one()
        )

        assert (
            persisted_snapshot.monitored_page_id
            == page.id
        )

        assert (
            persisted_snapshot.organization_id
            == organization.id
        )

        assert (
            db.query(
                MonitoredPage
            )
            .filter(
                MonitoredPage.organization_id
                == organization.id
            )
            .count()
            == 1
        )

        assert (
            db.query(
                Entity
            )
            .filter(
                Entity.organization_id
                == organization.id,
                Entity.domain
                == "ignored.example",
            )
            .count()
            == 0
        )

    finally:
        organization_id = (
            organization.id
        )

        if organization_id is not None:
            _cleanup(
                db,
                organization_ids=[
                    organization_id
                ],
            )

        db.close()


def test_scheduled_snapshot_rejects_cross_tenant_page(
    monkeypatch,
    tmp_path,
):
    db = SessionLocal()

    organization_a = _organization(
        suffix="tenant-a"
    )

    organization_b = _organization(
        suffix="tenant-b"
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

        page_a = _create_page(
            db,
            organization=organization_a,
        )

        content = (
            "Cross-tenant scheduled binding "
            "must fail closed."
        )

        snapshot_file = _snapshot_file(
            monkeypatch,
            tmp_path,
            content=content,
        )

        before_count = (
            db.query(
                PageSnapshot
            )
            .filter(
                PageSnapshot.organization_id
                == organization_b.id
            )
            .count()
        )

        with pytest.raises(
            ValueError,
            match=(
                "Scheduled monitored page does not exist "
                "for the organization"
            ),
        ):
            persistence.create_snapshot_record(
                db=db,
                organization_id=organization_b.id,
                entity_name="Google",
                entity_domain="google.com",
                entity_category="Technology",
                url=page_a.url,
                page_type=page_a.page_type,
                content_hash=_sha256(
                    content
                ),
                snapshot_file=snapshot_file,
                monitored_page_id=page_a.id,
            )

        db.rollback()

        after_count = (
            db.query(
                PageSnapshot
            )
            .filter(
                PageSnapshot.organization_id
                == organization_b.id
            )
            .count()
        )

        assert after_count == before_count

    finally:
        organization_ids = [
            organization_id
            for organization_id in (
                organization_a.id,
                organization_b.id,
            )
            if organization_id is not None
        ]

        if organization_ids:
            _cleanup(
                db,
                organization_ids=organization_ids,
            )

        db.close()


def test_scheduled_snapshot_rejects_url_mismatch(
    monkeypatch,
    tmp_path,
):
    db = SessionLocal()

    organization = _organization(
        suffix="url"
    )

    try:
        db.add(
            organization
        )
        db.commit()
        db.refresh(
            organization
        )

        page = _create_page(
            db,
            organization=organization,
        )

        content = (
            "Scheduled URL mismatch "
            "must fail closed."
        )

        snapshot_file = _snapshot_file(
            monkeypatch,
            tmp_path,
            content=content,
        )

        before_count = (
            db.query(
                PageSnapshot
            )
            .filter(
                PageSnapshot.organization_id
                == organization.id
            )
            .count()
        )

        with pytest.raises(
            ValueError,
            match=(
                "Scheduled monitored page URL does not "
                "match the crawler URL"
            ),
        ):
            persistence.create_snapshot_record(
                db=db,
                organization_id=organization.id,
                entity_name="Google",
                entity_domain="google.com",
                entity_category="Technology",
                url="https://policies.google.com/terms",
                page_type=page.page_type,
                content_hash=_sha256(
                    content
                ),
                snapshot_file=snapshot_file,
                monitored_page_id=page.id,
            )

        db.rollback()

        after_count = (
            db.query(
                PageSnapshot
            )
            .filter(
                PageSnapshot.organization_id
                == organization.id
            )
            .count()
        )

        assert after_count == before_count

    finally:
        organization_id = (
            organization.id
        )

        if organization_id is not None:
            _cleanup(
                db,
                organization_ids=[
                    organization_id
                ],
            )

        db.close()


def test_scheduled_snapshot_rejects_page_type_mismatch(
    monkeypatch,
    tmp_path,
):
    db = SessionLocal()

    organization = _organization(
        suffix="page-type"
    )

    try:
        db.add(
            organization
        )
        db.commit()
        db.refresh(
            organization
        )

        page = _create_page(
            db,
            organization=organization,
        )

        content = (
            "Scheduled page-type mismatch "
            "must fail closed."
        )

        snapshot_file = _snapshot_file(
            monkeypatch,
            tmp_path,
            content=content,
        )

        before_count = (
            db.query(
                PageSnapshot
            )
            .filter(
                PageSnapshot.organization_id
                == organization.id
            )
            .count()
        )

        with pytest.raises(
            ValueError,
            match=(
                "Scheduled monitored page type does not "
                "match the crawler page type"
            ),
        ):
            persistence.create_snapshot_record(
                db=db,
                organization_id=organization.id,
                entity_name="Google",
                entity_domain="google.com",
                entity_category="Technology",
                url=page.url,
                page_type="tos",
                content_hash=_sha256(
                    content
                ),
                snapshot_file=snapshot_file,
                monitored_page_id=page.id,
            )

        db.rollback()

        after_count = (
            db.query(
                PageSnapshot
            )
            .filter(
                PageSnapshot.organization_id
                == organization.id
            )
            .count()
        )

        assert after_count == before_count

    finally:
        organization_id = (
            organization.id
        )

        if organization_id is not None:
            _cleanup(
                db,
                organization_ids=[
                    organization_id
                ],
            )

        db.close()