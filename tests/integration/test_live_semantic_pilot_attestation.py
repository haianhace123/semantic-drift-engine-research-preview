"""PostgreSQL acceptance for scoped, read-only live pilot evidence retrieval.

Only test-owned organizations and files are used; no network or model calls.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from scripts.verify_live_semantic_pilot import assess_checkpoint, read_persisted_source
from src.core.database import SessionLocal
from src.models import Organization, PageSnapshot, SemanticDriftEvent
from src.services.public_source_enrollment_service import (
    enroll_public_source, validate_public_source,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _organization(db):
    suffix = uuid.uuid4().hex
    org = Organization(
        name=f"Pilot verifier {suffix}", slug=f"pilot-verifier-{suffix[:24]}",
        billing_email=f"pilot-{suffix[:12]}@example.test",
        quota_urls=5, quota_requests=100, is_active=True,
    )
    db.add(org)
    db.commit()
    db.refresh(org)
    return org.id


def _snapshot_file(path, content):
    path.write_text(content, encoding="utf-8")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_read_only_pilot_checkpoint_is_tenant_scoped_and_requires_actual_artifacts(tmp_path):
    with SessionLocal() as db:
        org_id = _organization(db)
        other_org_id = _organization(db)
        page = enroll_public_source(
            db, organization_id=org_id,
            source=validate_public_source(
                url=f"https://docs.pilot-example.org/policy-{uuid.uuid4().hex}",
                label="Pilot policy", page_type="docs", crawl_frequency="daily",
            ),
        )
        page_id = page.id
    try:
        assert read_persisted_source(organization_id=other_org_id, source_id=page_id) is None
        initial = read_persisted_source(organization_id=org_id, source_id=page_id)
        assert assess_checkpoint(phase="baseline", evidence=initial)["reason"] == "awaiting_baseline"

        old_content = "Records are retained for 30 days. " * 8
        new_content = "Records are retained for 90 days. " * 8
        old_file, new_file = tmp_path / "old.txt", tmp_path / "new.txt"
        old_hash = _snapshot_file(old_file, old_content)
        new_hash = _snapshot_file(new_file, new_content)
        started = datetime(2026, 10, 1, tzinfo=timezone.utc)
        with SessionLocal() as db:
            old = PageSnapshot(
                organization_id=org_id, monitored_page_id=page_id,
                content_hash=old_hash, raw_content_path=str(old_file),
                crawled_at=started,
            )
            db.add(old)
            db.commit()
            old_id = old.id
        baseline = read_persisted_source(organization_id=org_id, source_id=page_id)
        assert assess_checkpoint(phase="baseline", evidence=baseline)["outcome"] == "pass"

        with SessionLocal() as db:
            new = PageSnapshot(
                organization_id=org_id, monitored_page_id=page_id,
                content_hash=new_hash, raw_content_path=str(new_file),
                crawled_at=started + timedelta(hours=1),
            )
            db.add(new)
            db.commit()
            new_id = new.id
        before_event = read_persisted_source(organization_id=org_id, source_id=page_id)
        assert assess_checkpoint(
            phase="changed", evidence=before_event,
            baseline_id=str(old_id), expect="material",
        )["reason"] == "semantic_event_missing"
        with SessionLocal() as db:
            event = SemanticDriftEvent(
                organization_id=org_id, monitored_page_id=page_id,
                old_snapshot_id=old_id, new_snapshot_id=new_id,
                analysis_status="completed", changes_count=1,
                risk_level="medium",
                semantic_delta_json={
                    "change_detected": True,
                    "old_hash": old_hash, "new_hash": new_hash,
                    "semantic_analysis": {
                        "analysis_status": "success", "overall_risk": "medium",
                        "summary": "Retention increased",
                        "changes": [{
                            "category": "policy", "old_meaning": "30 days",
                            "new_meaning": "90 days", "impact": "Longer retention",
                            "risk_level": "medium",
                        }],
                    },
                },
            )
            db.add(event)
            db.commit()
        evidence = read_persisted_source(organization_id=org_id, source_id=page_id)
        result = assess_checkpoint(
            phase="changed", evidence=evidence,
            baseline_id=str(old_id), expect="material",
        )
        assert result["outcome"] == "pass"
        assert result["verifier_provider_calls"] is False
        assert read_persisted_source(organization_id=other_org_id, source_id=page_id) is None
        new_file.write_text("tampered", encoding="utf-8")
        corrupt = read_persisted_source(organization_id=org_id, source_id=page_id)
        assert assess_checkpoint(
            phase="changed", evidence=corrupt,
            baseline_id=str(old_id), expect="material",
        )["reason"] == "snapshot_artifact_not_verified"
    finally:
        with SessionLocal() as db:
            db.query(Organization).filter(
                Organization.id.in_([org_id, other_org_id])
            ).delete(synchronize_session=False)
            db.commit()
