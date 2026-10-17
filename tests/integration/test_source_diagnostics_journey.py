"""Customer source evidence: tenant isolation and two-crawl drift acceptance.

Only local PostgreSQL and temporary snapshot files are used. HTML and the
semantic provider are controlled; no public site or external model is called.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import src.main as main_module
from src.api.routes import sources as source_routes
from src.core.database import SessionLocal
from src.core.security import CSRF_COOKIE, get_current_user
from src.models import Organization, PageSnapshot, SemanticDriftEvent
from src.services.public_source_enrollment_service import (
    enroll_public_source, validate_public_source,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _new_org(db):
    suffix = uuid.uuid4().hex
    org = Organization(
        name=f"Source journey {suffix}", slug=f"source-journey-{suffix[:24]}",
        billing_email=f"source-{suffix[:12]}@example.test",
        quota_urls=5, quota_requests=100, is_active=True,
    )
    db.add(org)
    db.commit()
    db.refresh(org)
    return org.id


def _source(db, org_id, path):
    return enroll_public_source(
        db, organization_id=org_id,
        source=validate_public_source(
            url=f"https://docs.journey-example.org/{path}",
            label="Pilot documents", page_type="docs", crawl_frequency="hourly",
        ),
    )


def _cleanup(*org_ids):
    with SessionLocal() as db:
        db.query(Organization).filter(Organization.id.in_(org_ids)).delete(synchronize_session=False)
        db.commit()


def _client(monkeypatch, org_id):
    monkeypatch.setattr(main_module, "verify_token", lambda token: {"sub": str(uuid.uuid4())})
    app = main_module.app

    def override_db():
        with SessionLocal() as db:
            yield db

    app.dependency_overrides[source_routes.get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(), organization_id=org_id, role="viewer",
    )
    client = TestClient(app)
    client.cookies.set(CSRF_COOKIE, "pilot-source-csrf")
    return client


def _get(client, page_id):
    return client.get(
        f"/api/sources/{page_id}/diagnostics",
        headers={"Authorization": "Bearer pilot-test-token"},
    )


def test_diagnostics_separates_no_evidence_unchanged_missing_event_and_failed_analysis(monkeypatch):
    with SessionLocal() as db:
        org_a = _new_org(db)
        org_b = _new_org(db)
        page = _source(db, org_a, f"states-{uuid.uuid4().hex}")
        page_id = page.id
    client = _client(monkeypatch, org_a)
    app = main_module.app
    try:
        response = _get(client, page_id)
        assert response.status_code == 200, response.text
        assert response.json()["latest_snapshot"] is None
        assert response.json()["latest_comparison"] is None
        assert _get(client, uuid.uuid4()).status_code == 404

        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with SessionLocal() as db:
            first = PageSnapshot(
                organization_id=org_a, monitored_page_id=page_id,
                content_hash="a" * 64, crawled_at=start,
            )
            db.add(first)
            db.commit()
        assert _get(client, page_id).json()["latest_comparison"] is None

        with SessionLocal() as db:
            same = PageSnapshot(
                organization_id=org_a, monitored_page_id=page_id,
                content_hash="a" * 64, crawled_at=start + timedelta(minutes=1),
            )
            db.add(same)
            db.commit()
            same_id = same.id
        comparison = _get(client, page_id).json()["latest_comparison"]
        assert comparison["content_changed"] is False
        assert comparison["analysis_status"] is None

        with SessionLocal() as db:
            changed = PageSnapshot(
                organization_id=org_a, monitored_page_id=page_id,
                content_hash="b" * 64, crawled_at=start + timedelta(minutes=2),
            )
            db.add(changed)
            db.commit()
            changed_id = changed.id
        comparison = _get(client, page_id).json()["latest_comparison"]
        assert comparison["content_changed"] is True
        assert comparison["event_id"] is None
        assert comparison["analysis_status"] is None

        with SessionLocal() as db:
            failed = SemanticDriftEvent(
                organization_id=org_a, monitored_page_id=page_id,
                old_snapshot_id=same_id, new_snapshot_id=changed_id,
                semantic_delta_json={"change_detected": True},
                analysis_status="failed", analysis_error="private-provider-detail",
            )
            db.add(failed)
            db.commit()
            failed_id = failed.id
        response = _get(client, page_id)
        assert response.status_code == 200
        comparison = response.json()["latest_comparison"]
        assert comparison["event_id"] == str(failed_id)
        assert comparison["analysis_status"] == "failed"
        assert "private-provider-detail" not in response.text

        # A user from another tenant sees neither source nor its event IDs.
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=uuid.uuid4(), organization_id=org_b, role="viewer",
        )
        assert _get(client, page_id).status_code == 404
    finally:
        client.close()
        app.dependency_overrides.pop(source_routes.get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        _cleanup(org_a, org_b)


def test_enroll_scheduler_two_snapshots_semantic_event_and_diagnostics(monkeypatch, tmp_path):
    from src.crawler import persistence, public_html_crawler, scheduler
    from src.detector import change_detector
    from src.crawler.scheduler import CrawlCycleStatus

    with SessionLocal() as db:
        org_id = _new_org(db)
    app = main_module.app
    client = _client(monkeypatch, org_id)
    body = {"url": f"https://docs.journey-example.org/policy-{uuid.uuid4().hex}",
            "label": "Customer policy", "page_type": "docs", "crawl_frequency": "hourly"}
    headers = {"Authorization": "Bearer pilot-test-token", "X-CSRF-Token": "pilot-source-csrf"}
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(), organization_id=org_id, role="admin",
    )
    html = ["<main>" + ("Refunds permitted for 30 days. " * 16) + "</main>"]
    monkeypatch.setattr(persistence.Paths, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(public_html_crawler, "fetch_crawler_html", lambda url, **kwargs: html[0])

    def stub_semantic_analysis(*args, **kwargs):
        return ({
            "chunks_analyzed": 1,
            "semantic_analysis": {
                "analysis_status": "success",
                "changes": [{
                    "category": "policy", "old_meaning": "Refunds for 30 days",
                    "new_meaning": "Refunds for 3 days",
                    "impact": "Reduced return window", "risk_level": "high",
                }],
                "summary": "Refund period shortened", "overall_risk": "high",
            },
        }, "Refund period shortened")

    monkeypatch.setattr(change_detector, "_process_semantic_analysis", stub_semantic_analysis)
    try:
        enrolled = client.post("/api/sources", headers=headers, json=body)
        assert enrolled.status_code == 201, enrolled.text
        source_id = uuid.UUID(enrolled.json()["id"])
        assert _get(client, source_id).json()["latest_snapshot"] is None

        first = scheduler.run_crawl_cycle(organization_id=org_id)
        assert first.status == CrawlCycleStatus.SUCCEEDED
        assert first.claim.monitored_page_id == source_id
        baseline = _get(client, source_id).json()
        assert baseline["latest_snapshot"] is not None
        assert baseline["latest_comparison"] is None

        # Advance only this test source to due; never wait an hour or change
        # the global scheduler configuration.
        with SessionLocal() as db:
            from src.models import MonitoredPage
            page = db.query(MonitoredPage).filter(
                MonitoredPage.id == source_id, MonitoredPage.organization_id == org_id,
            ).with_for_update().one()
            page.next_crawl_at = None
            db.commit()
        html[0] = "<main>" + ("Refunds permitted for 3 days. " * 16) + "</main>"
        second = scheduler.run_crawl_cycle(organization_id=org_id)
        assert second.status == CrawlCycleStatus.SUCCEEDED
        assert second.claim.monitored_page_id == source_id
        evidence = _get(client, source_id).json()
        comparison = evidence["latest_comparison"]
        assert comparison["content_changed"] is True
        assert comparison["event_id"] is not None
        assert comparison["analysis_status"] == "completed"
        with SessionLocal() as db:
            event = db.get(SemanticDriftEvent, uuid.UUID(comparison["event_id"]))
            assert event is not None
            assert event.organization_id == org_id
            assert event.old_snapshot_id == uuid.UUID(comparison["old_snapshot_id"])
            assert event.new_snapshot_id == uuid.UUID(comparison["new_snapshot_id"])
            assert event.risk_level == "high"
            assert db.query(PageSnapshot).filter(
                PageSnapshot.organization_id == org_id,
                PageSnapshot.monitored_page_id == source_id,
            ).count() == 2
    finally:
        client.close()
        app.dependency_overrides.pop(source_routes.get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        _cleanup(org_id)
