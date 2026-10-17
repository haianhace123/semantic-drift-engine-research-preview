"""PostgreSQL acceptance for scheduled crawler snapshot write fencing."""

from __future__ import annotations

from datetime import timedelta
import os
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from src.core.database import SessionLocal
from src.crawler.persistence import (
    SnapshotFile,
    _resolve_scheduled_monitored_page,
    create_snapshot_record,
)
from src.models import Entity, MonitoredPage, Organization, PageSnapshot
from src.services.crawl_scheduling_service import (
    CrawlClaimAuthorityError,
    claim_due_monitored_page,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _page(db, *, prefix: str) -> tuple[uuid.UUID, uuid.UUID, str]:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        id=uuid.uuid4(),
        name=f"Claim fence {prefix} {suffix}",
        slug=f"claim-fence-{prefix}-{suffix}",
        billing_email=f"claim-fence-{suffix}@example.test",
        subscription_tier="free",
        quota_urls=10,
        quota_requests=1000,
        is_active=True,
    )
    db.add(organization)
    db.flush()
    entity = Entity(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name="Policy source",
        domain=f"claim-{suffix}.example.test",
        category="Technology",
    )
    db.add(entity)
    db.flush()
    url = f"https://openai.com/policies/{suffix}"
    page = MonitoredPage(
        id=uuid.uuid4(),
        organization_id=organization.id,
        entity_id=entity.id,
        url=url,
        page_type="tos",
        crawl_frequency="daily",
        is_active=True,
        next_crawl_at=None,
    )
    db.add(page)
    db.commit()
    return organization.id, page.id, url


def _persist(db, *, org, page, url, token, tmp_path):
    filename = tmp_path / f"{uuid.uuid4()}.txt"
    filename.write_text("Evidence for scheduled claim fence", encoding="utf-8")
    return create_snapshot_record(
        db=db,
        organization_id=org,
        entity_name="Policy source",
        entity_domain="claim.example.test",
        entity_category="Technology",
        url=url,
        page_type="tos",
        content_hash="f" * 64,
        snapshot_file=SnapshotFile(path=str(filename), size_bytes=32),
        monitored_page_id=page,
        claim_token=token,
    )


def _remove_org(db, org):
    db.rollback()
    db.query(Organization).filter(Organization.id == org).delete()
    db.commit()


def test_reclaimed_claim_cannot_write_snapshot_but_new_claim_can(tmp_path):
    db = SessionLocal()
    org = None
    try:
        org, page, url = _page(db, prefix="reclaim")
        first = claim_due_monitored_page(db, organization_id=org, lease_seconds=900)
        assert first is not None
        db.commit()

        # Simulate lease aging deterministically using PostgreSQL server time.
        row = db.query(MonitoredPage).filter(MonitoredPage.id == page).with_for_update().one()
        now = db.execute(select(func.clock_timestamp())).scalar_one()
        row.crawl_claimed_at = now - timedelta(minutes=20)
        row.crawl_claim_expires_at = now - timedelta(minutes=10)
        db.commit()

        second = claim_due_monitored_page(db, organization_id=org, lease_seconds=900)
        assert second is not None
        assert second.claim_token != first.claim_token
        db.commit()

        with pytest.raises(CrawlClaimAuthorityError):
            _persist(db, org=org, page=page, url=url, token=first.claim_token, tmp_path=tmp_path)
        db.rollback()
        assert db.query(PageSnapshot).filter(PageSnapshot.monitored_page_id == page).count() == 0

        # A manual code path must not bypass an active scheduler claim.
        with pytest.raises(CrawlClaimAuthorityError):
            _persist(db, org=org, page=page, url=url, token=None, tmp_path=tmp_path)
        db.rollback()
        assert db.query(PageSnapshot).filter(PageSnapshot.monitored_page_id == page).count() == 0

        entity_domain = db.query(Entity.domain).join(
            MonitoredPage, MonitoredPage.entity_id == Entity.id
        ).filter(MonitoredPage.id == page).scalar()
        assert entity_domain is not None
        with pytest.raises(CrawlClaimAuthorityError):
            create_snapshot_record(
                db=db,
                organization_id=org,
                entity_name="Policy source",
                entity_domain=entity_domain,
                entity_category="Technology",
                url=url,
                page_type="tos",
                content_hash="f" * 64,
                snapshot_file=SnapshotFile(path=str(tmp_path / "manual.txt"), size_bytes=1),
            )
        db.rollback()
        assert db.query(PageSnapshot).filter(PageSnapshot.monitored_page_id == page).count() == 0

        snapshot = _persist(db, org=org, page=page, url=url, token=second.claim_token, tmp_path=tmp_path)
        assert snapshot.organization_id == org
        assert snapshot.monitored_page_id == page
        assert db.query(PageSnapshot).filter(PageSnapshot.monitored_page_id == page).count() == 1
    finally:
        if org is not None:
            _remove_org(db, org)
        db.close()


def test_expired_claim_cannot_write_without_reclaim(tmp_path):
    db = SessionLocal()
    org = None
    try:
        org, page, url = _page(db, prefix="expired")
        claim = claim_due_monitored_page(db, organization_id=org, lease_seconds=900)
        assert claim is not None
        db.commit()
        row = db.query(MonitoredPage).filter(MonitoredPage.id == page).with_for_update().one()
        now = db.execute(select(func.clock_timestamp())).scalar_one()
        row.crawl_claimed_at = now - timedelta(minutes=20)
        row.crawl_claim_expires_at = now - timedelta(minutes=10)
        db.commit()

        with pytest.raises(CrawlClaimAuthorityError):
            _persist(db, org=org, page=page, url=url, token=claim.claim_token, tmp_path=tmp_path)
        db.rollback()
        assert db.query(PageSnapshot).filter(PageSnapshot.monitored_page_id == page).count() == 0
    finally:
        if org is not None:
            _remove_org(db, org)
        db.close()


def test_cross_tenant_claim_cannot_write_snapshot(tmp_path):
    db = SessionLocal()
    org_a = org_b = None
    try:
        org_a, page_a, url_a = _page(db, prefix="tenant-a")
        org_b, _, _ = _page(db, prefix="tenant-b")
        claim = claim_due_monitored_page(db, organization_id=org_a)
        assert claim is not None
        db.commit()
        with pytest.raises(ValueError, match="does not exist"):
            _persist(db, org=org_b, page=page_a, url=url_a, token=claim.claim_token, tmp_path=tmp_path)
        db.rollback()
        assert db.query(PageSnapshot).filter(PageSnapshot.monitored_page_id == page_a).count() == 0
    finally:
        for org in (org_b, org_a):
            if org is not None:
                _remove_org(db, org)
        db.close()


def test_snapshot_identity_validation_holds_row_lock_until_commit():
    db = SessionLocal()
    contender = SessionLocal()
    org = None
    try:
        org, page, url = _page(db, prefix="serialize")
        claim = claim_due_monitored_page(db, organization_id=org)
        assert claim is not None
        db.commit()

        _resolve_scheduled_monitored_page(
            db=db,
            organization_id=org,
            monitored_page_id=page,
            url=url,
            page_type="tos",
            claim_token=claim.claim_token,
        )

        # Reclaim is also a FOR UPDATE on the same row. It cannot alter
        # the token until the snapshot write transaction commits.
        with pytest.raises(OperationalError):
            contender.query(MonitoredPage).filter(
                MonitoredPage.id == page
            ).with_for_update(nowait=True).one()
        contender.rollback()
    finally:
        db.rollback()
        contender.close()
        if org is not None:
            _remove_org(db, org)
        db.close()
