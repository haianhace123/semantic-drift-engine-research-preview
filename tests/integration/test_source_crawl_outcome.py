"""PostgreSQL acceptance: finalized crawler outcome, retry and tenant boundary."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import src.main as main_module
from src.api.routes import sources as source_routes
from src.core.database import SessionLocal
from src.core.security import get_current_user
from src.crawler import scheduler
from src.crawler.public_html_crawler import PublicHTMLCrawlFailure
from src.models import MonitoredPage, Organization
from src.services.crawl_scheduling_service import (
    CrawlClaimAuthorityError, finalize_crawl_failure,
)
from src.services.public_source_enrollment_service import enroll_public_source, validate_public_source

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _organization(db):
    suffix = uuid.uuid4().hex
    org = Organization(
        name=f"Crawl outcome {suffix}", slug=f"crawl-outcome-{suffix[:22]}",
        billing_email=f"crawl-outcome-{suffix[:10]}@example.test",
        is_active=True, quota_urls=5, quota_requests=100,
    )
    db.add(org)
    db.commit()
    return org.id


def _db_override():
    with SessionLocal() as db:
        yield db


def test_crawl_failure_retry_success_and_cross_tenant_status(monkeypatch):
    with SessionLocal() as db:
        org_a = _organization(db)
        org_b = _organization(db)
        page = enroll_public_source(
            db, organization_id=org_a,
            source=validate_public_source(
                url=f"https://docs.outcome-example.org/policy-{uuid.uuid4().hex}",
                label="Outcome test", page_type="docs", crawl_frequency="hourly",
            ),
        )
        page_id = page.id
    app = main_module.app
    monkeypatch.setattr(main_module, "verify_token", lambda token: {"sub": str(uuid.uuid4())})
    app.dependency_overrides[source_routes.get_db] = _db_override
    current_org = org_a
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(), organization_id=current_org, role="viewer",
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer source-test-token"}
    try:
        def listing():
            response = client.get("/api/sources", headers=headers)
            assert response.status_code == 200, response.text
            return response.json()["items"]
        assert listing()[0]["last_crawl_status"] is None
        assert listing()[0]["last_crawl_attempt_at"] is None
        assert listing()[0]["consecutive_crawl_failures"] == 0
        def failed_fetch(**kwargs):
            raise PublicHTMLCrawlFailure("fetch_failed")
        monkeypatch.setattr(scheduler, "crawl_monitored_page", failed_fetch)
        failed = scheduler.run_crawl_cycle(organization_id=org_a)
        assert failed.status == scheduler.CrawlCycleStatus.FAILED
        entry = listing()[0]
        assert entry["last_crawl_status"] == "failed"
        assert entry["last_crawl_failure_code"] == "fetch_failed"
        assert entry["consecutive_crawl_failures"] == 1
        assert entry["last_crawl_attempt_at"] is not None
        assert entry["next_crawl_at"] is not None
        assert "private" not in str(entry)
        current_org = org_b
        assert listing() == []
        current_org = org_a
        with SessionLocal() as db:
            row = db.query(MonitoredPage).filter(MonitoredPage.id == page_id).with_for_update().one()
            row.next_crawl_at = None
            db.commit()
        monkeypatch.setattr(scheduler, "crawl_monitored_page", lambda **kwargs: object())
        succeeded = scheduler.run_crawl_cycle(organization_id=org_a)
        assert succeeded.status == scheduler.CrawlCycleStatus.SUCCEEDED
        entry = listing()[0]
        assert entry["last_crawl_status"] == "succeeded"
        assert entry["last_crawl_failure_code"] is None
        assert entry["consecutive_crawl_failures"] == 0
        assert entry["last_crawled_at"] is not None
        # An older claim cannot overwrite a newer finalized outcome.
        with SessionLocal() as db:
            with pytest.raises(CrawlClaimAuthorityError):
                finalize_crawl_failure(
                    db, monitored_page_id=page_id, claim_token=failed.claim.claim_token,
                    failure_code="blocked_destination",
                )
            db.rollback()
        assert listing()[0]["last_crawl_status"] == "succeeded"
    finally:
        client.close()
        app.dependency_overrides.pop(source_routes.get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        with SessionLocal() as db:
            db.query(Organization).filter(Organization.id.in_([org_a, org_b])).delete(synchronize_session=False)
            db.commit()
