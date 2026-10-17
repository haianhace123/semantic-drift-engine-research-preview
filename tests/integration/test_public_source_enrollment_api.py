"""Public HTML source enrollment, tenant and scheduler contract acceptance."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import src.main as main_module
from src.api.routes import sources as source_routes
from src.core.database import SessionLocal
from src.core.security import get_current_user, CSRF_COOKIE
from src.models import MonitoredPage, Organization, PageSnapshot
from src.services.crawl_scheduling_service import claim_due_monitored_page

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _organization(db, *, quota=3):
    suffix = uuid.uuid4().hex
    org = Organization(
        name=f"Public source pilot {suffix}",
        slug=f"source-pilot-{suffix[:20]}",
        billing_email=f"source-pilot-{suffix[:10]}@example.test",
        is_active=True, quota_urls=quota, quota_requests=100,
    )
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _database_override():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _headers():
    return {"Authorization": "Bearer source-pilot-test-token", "X-CSRF-Token": "public-source-csrf"}


def _cleanup(org_ids):
    db = SessionLocal()
    try:
        db.query(Organization).filter(Organization.id.in_(org_ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_public_source_lifecycle_quota_and_tenant_isolation(monkeypatch):
    monkeypatch.setattr(main_module, "verify_token", lambda token: {"sub": str(uuid.uuid4())})
    db = SessionLocal()
    ids = []
    try:
        org_a = _organization(db, quota=1)
        org_b = _organization(db, quota=2)
        ids = [org_a.id, org_b.id]
    finally:
        db.close()
    role = "admin"
    current_org = org_a.id
    source_routes_get_db = source_routes.get_db
    app = main_module.app
    app.dependency_overrides[source_routes_get_db] = _database_override
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(), organization_id=current_org, role=role,
    )
    client = TestClient(app)
    client.cookies.set(CSRF_COOKIE, "public-source-csrf")
    try:
        body = {
            "url": "https://docs.example.org/terms",
            "label": "Example Docs", "page_type": "tos",
            "crawl_frequency": "daily",
        }
        assert client.post("/api/sources", headers={"Authorization": "Bearer source-pilot-test-token"}, json=body).status_code == 403
        created = client.post("/api/sources", headers=_headers(), json=body)
        assert created.status_code == 201, created.text
        owned = created.json()
        assert owned["url"] == body["url"]
        assert owned["is_active"] is True
        assert owned["next_crawl_at"] is None
        duplicate = client.post("/api/sources", headers=_headers(), json=body)
        assert duplicate.status_code == 409
        quota = client.post("/api/sources", headers=_headers(), json={
            **body, "url": "https://docs.example.org/privacy", "page_type": "privacy",
        })
        assert quota.status_code == 409
        listing = client.get("/api/sources", headers=_headers())
        assert listing.status_code == 200
        assert listing.json()["total"] == 1
        assert listing.json()["items"][0]["snapshot_count"] == 0
        with SessionLocal() as snapshot_db:
            snapshot_db.add(PageSnapshot(
                monitored_page_id=uuid.UUID(owned["id"]),
                organization_id=org_a.id,
                content_hash="a" * 64,
                raw_content="baseline",
            ))
            snapshot_db.commit()
        observed = client.get("/api/sources", headers=_headers()).json()
        assert observed["items"][0]["snapshot_count"] == 1
        no_csrf_frequency = client.post(
            f"/api/sources/{owned['id']}/frequency",
            headers={"Authorization": "Bearer source-pilot-test-token"},
            json={"crawl_frequency": "weekly"},
        )
        assert no_csrf_frequency.status_code == 403
        changed = client.post(
            f"/api/sources/{owned['id']}/frequency",
            headers=_headers(), json={"crawl_frequency": "weekly"},
        )
        assert changed.status_code == 200
        assert changed.json()["crawl_frequency"] == "weekly"
        assert changed.json()["next_crawl_at"] is None
        assert client.post(
            f"/api/sources/{owned['id']}/frequency", headers=_headers(),
            json={"crawl_frequency": "invalid"},
        ).status_code == 422
        role = "viewer"
        assert client.post("/api/sources", headers=_headers(), json=body).status_code == 403
        assert client.post(f"/api/sources/{owned['id']}/pause", headers=_headers()).status_code == 403
        assert client.post(
            f"/api/sources/{owned['id']}/frequency", headers=_headers(),
            json={"crawl_frequency": "daily"},
        ).status_code == 403
        role = "admin"
        current_org = org_b.id
        assert client.get("/api/sources", headers=_headers()).json()["total"] == 0
        assert client.post(f"/api/sources/{owned['id']}/pause", headers=_headers()).status_code == 404
        assert client.post(f"/api/sources/{owned['id']}/resume", headers=_headers()).status_code == 404
        assert client.post(
            f"/api/sources/{owned['id']}/frequency", headers=_headers(),
            json={"crawl_frequency": "daily"},
        ).status_code == 404
        created_b = client.post("/api/sources", headers=_headers(), json=body)
        assert created_b.status_code == 201
        assert created_b.json()["id"] != owned["id"]
        assert client.get("/api/sources", headers=_headers()).json()["items"][0]["snapshot_count"] == 0
        current_org = org_a.id
        paused = client.post(f"/api/sources/{owned['id']}/pause", headers=_headers())
        assert paused.status_code == 200
        assert paused.json()["is_active"] is False
        resumed = client.post(f"/api/sources/{owned['id']}/resume", headers=_headers())
        assert resumed.status_code == 200
        assert resumed.json()["is_active"] is True
        # The scheduled crawler can claim the enrolled source using its
        # existing tenant and lease authority without a custom queue.
        with SessionLocal() as claim_db:
            claim = claim_due_monitored_page(claim_db, organization_id=org_a.id)
            assert claim is not None
            assert claim.monitored_page_id == uuid.UUID(owned["id"])
            assert claim.url == body["url"]
            claim_db.commit()
        claimed_change = client.post(
            f"/api/sources/{owned['id']}/frequency", headers=_headers(),
            json={"crawl_frequency": "hourly"},
        )
        assert claimed_change.status_code == 409
    finally:
        client.close()
        app.dependency_overrides.pop(source_routes_get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        _cleanup(ids)


def test_public_html_crawler_persists_claim_fenced_snapshot_for_enrolled_tenant(monkeypatch, tmp_path):
    from src.crawler import persistence, public_html_crawler
    from src.models import PageSnapshot
    from src.services.public_source_enrollment_service import (
        enroll_public_source, validate_public_source,
    )

    db = SessionLocal()
    org_ids = []
    try:
        org = _organization(db, quota=2)
        other = _organization(db, quota=2)
        org_ids = [org.id, other.id]
        source = validate_public_source(
            url="https://docs.pilot-example.org/privacy",
            page_type="privacy", crawl_frequency="hourly", label="Pilot policy",
        )
        page = enroll_public_source(db, organization_id=org.id, source=source)
        page_id = page.id
        claim = claim_due_monitored_page(db, organization_id=org.id)
        assert claim is not None and claim.monitored_page_id == page_id
        db.commit()
        monkeypatch.setattr(persistence.Paths, "get_data_dir", lambda: tmp_path)
        monkeypatch.setattr(
            public_html_crawler, "fetch_crawler_html",
            lambda url, **kwargs: "<main>" + ("Public policy content. " * 16) + "</main>",
        )
        monkeypatch.setattr(public_html_crawler, "check_and_create_drift", lambda page_id: None)
        crawler = public_html_crawler.PublicHTMLCrawler(
            url=source.url, organization_id=org.id,
            monitored_page_id=page_id, claim_token=claim.claim_token,
        )
        assert crawler.crawl() is not None
        with SessionLocal() as check:
            assert check.query(PageSnapshot).filter(
                PageSnapshot.organization_id == org.id,
                PageSnapshot.monitored_page_id == page_id,
            ).count() == 1
            assert check.query(PageSnapshot).filter(
                PageSnapshot.organization_id == other.id,
                PageSnapshot.monitored_page_id == page_id,
            ).count() == 0
        # A different organization may not reuse the same claim/page pair.
        cross_tenant = public_html_crawler.PublicHTMLCrawler(
            url=source.url, organization_id=other.id,
            monitored_page_id=page_id, claim_token=claim.claim_token,
        )
        assert cross_tenant.crawl() is None
        # A stale token cannot commit another observation.
        stale = public_html_crawler.PublicHTMLCrawler(
            url=source.url, organization_id=org.id,
            monitored_page_id=page_id, claim_token=uuid.uuid4(),
        )
        assert stale.crawl() is None
        with SessionLocal() as check:
            assert check.query(PageSnapshot).filter(
                PageSnapshot.organization_id == org.id,
                PageSnapshot.monitored_page_id == page_id,
            ).count() == 1
    finally:
        db.close()
        if org_ids:
            _cleanup(org_ids)
