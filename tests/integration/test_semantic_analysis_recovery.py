"""PostgreSQL integration tests for in-place semantic analysis recovery."""

from __future__ import annotations

import hashlib
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from src.core.database import SessionLocal
from src.models.entity import Entity
from src.models.organization import Organization
from src.models.monitored_page import MonitoredPage
from src.models.page_snapshot import PageSnapshot
from src.models.semantic_drift_event import SemanticDriftEvent
from src.services import semantic_analysis_recovery_service as recovery

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="Database integration tests are disabled.",
)


@pytest.fixture
def failed_event(tmp_path):
    suffix = uuid.uuid4().hex
    org_id, entity_id, page_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    old_id, new_id, event_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    paths = (tmp_path / "old.txt", tmp_path / "new.txt")
    contents = ("Refunds are available for 30 days.", "Refunds are available for 3 days.")
    for path, content in zip(paths, contents):
        path.write_text(content, encoding="utf-8")
    db = SessionLocal()
    try:
        db.add(Organization(
            id=org_id, name="SDE recovery test", slug=f"recover-{suffix}",
            billing_email="test@example.invalid",
        ))
        db.flush()
        db.add(Entity(
            id=entity_id, organization_id=org_id,
            name="Recovery test", domain=f"{suffix}.example.invalid",
        ))
        db.flush()
        db.add(MonitoredPage(
            id=page_id, organization_id=org_id, entity_id=entity_id,
            url=f"https://{suffix}.example.invalid/policy", page_type="tos",
            crawl_frequency="daily", is_active=True,
        ))
        db.flush()
        for snapshot_id, path, content in zip((old_id, new_id), paths, contents):
            db.add(PageSnapshot(
                id=snapshot_id, monitored_page_id=page_id,
                organization_id=org_id, content_hash=hashlib.sha256(content.encode()).hexdigest(),
                raw_content_path=str(path),
            ))
        db.flush()
        db.add(SemanticDriftEvent(
            id=event_id, monitored_page_id=page_id, organization_id=org_id,
            old_snapshot_id=old_id, new_snapshot_id=new_id,
            semantic_delta_json={
                "semantic_analysis": {"analysis_status": "failed"},
                "analysis_failure": {"retryable": True},
            },
            summary_text="Provider was unavailable", risk_level="medium",
            confidence=0.0, impact_score=0, analysis_status="failed",
            analysis_error="DeepSeek semantic analysis request failed.",
            analysis_attempts=1,
            detected_at=datetime.now(timezone.utc) - timedelta(hours=1),
            analyzed_at=datetime.now(timezone.utc) - timedelta(hours=1),
        ))
        db.commit()
        yield org_id, event_id, paths
    finally:
        db.rollback()
        try:
            db.query(SemanticDriftEvent).filter(SemanticDriftEvent.id == event_id).delete()
            db.query(PageSnapshot).filter(PageSnapshot.id.in_([old_id, new_id])).delete(synchronize_session=False)
            db.query(MonitoredPage).filter(MonitoredPage.id == page_id).delete()
            db.query(Entity).filter(Entity.id == entity_id).delete()
            db.query(Organization).filter(Organization.id == org_id).delete()
            db.commit()
        finally:
            db.close()


def _provider_result(*args, **kwargs):
    return ({
        "chunks_analyzed": 1,
        "semantic_analysis": {
            "analysis_status": "success",
            "changes": [{
                "category": "policy", "old_meaning": "30 days", "new_meaning": "3 days",
                "impact": "Refund window reduced", "risk_level": "high",
            }],
            "summary": "Refund terms changed", "overall_risk": "high",
        },
    }, "Refund terms changed")


def test_recovers_same_event_without_cross_tenant_access(monkeypatch, failed_event):
    org_id, event_id, _ = failed_event
    other_org = uuid.uuid4()
    assert recovery.recover_event(event_id, organization_id=other_org).outcome == "not_found"
    monkeypatch.setattr(recovery, "_process_semantic_analysis", _provider_result)
    monkeypatch.setattr(recovery, "_enrich_relationships", lambda *args: None)
    result = recovery.recover_event(event_id, organization_id=org_id)
    assert result.outcome == "completed"
    assert result.attempts == 2
    db = SessionLocal()
    try:
        event = db.get(SemanticDriftEvent, event_id)
        assert event.id == event_id and event.organization_id == org_id
        assert event.analysis_status == "completed"
        assert event.analysis_attempts == 2 and event.risk_level == "high"
        assert event.analysis_error is None
        assert db.query(SemanticDriftEvent).filter(
            SemanticDriftEvent.old_snapshot_id == event.old_snapshot_id,
            SemanticDriftEvent.new_snapshot_id == event.new_snapshot_id,
        ).count() == 1
    finally:
        db.close()
    assert recovery.recover_event(event_id, organization_id=org_id).outcome == "not_due"


def test_same_event_cannot_run_provider_concurrently(monkeypatch, failed_event):
    org_id, event_id, _ = failed_event
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocked(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(10)
        return _provider_result()

    monkeypatch.setattr(recovery, "_process_semantic_analysis", blocked)
    monkeypatch.setattr(recovery, "_enrich_relationships", lambda *args: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(recovery.recover_event, event_id, organization_id=org_id)
        assert entered.wait(10)
        second = recovery.recover_event(event_id, organization_id=org_id)
        assert second.outcome == "locked"
        release.set()
        assert first.result(timeout=10).outcome == "completed"
    assert len(calls) == 1


def test_tampered_snapshot_blocks_recovery(monkeypatch, failed_event):
    org_id, event_id, paths = failed_event
    paths[0].write_text("bad bytes", encoding="utf-8")
    monkeypatch.setattr(
        recovery, "_process_semantic_analysis",
        lambda *args, **kwargs: pytest.fail("Provider called for tampered data"),
    )
    result = recovery.recover_event(event_id, organization_id=org_id)
    assert result.outcome == "failed"
    db = SessionLocal()
    try:
        event = db.get(SemanticDriftEvent, event_id)
        assert event.analysis_status == "failed"
        assert event.semantic_delta_json["analysis_failure"]["retryable"] is False
        assert event.analysis_attempts == 2
    finally:
        db.close()


def test_temporary_provider_failure_increments_attempt_once(monkeypatch, failed_event):
    org_id, event_id, _ = failed_event
    from src.diff_engine.deepseek_diff import DeepSeekTransientError
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise DeepSeekTransientError("provider timeout")

    monkeypatch.setattr(recovery, "_process_semantic_analysis", fail)
    result = recovery.recover_event(event_id, organization_id=org_id)
    assert result.outcome == "failed" and result.attempts == 2
    assert recovery.recover_event(event_id, organization_id=org_id).outcome == "not_due"
    assert len(calls) == 1
    db = SessionLocal()
    try:
        event = db.get(SemanticDriftEvent, event_id)
        assert event.analysis_status == "failed"
        assert event.confidence == 0.0
        assert event.analysis_attempts == 2
        assert event.semantic_delta_json["analysis_failure"]["retryable"] is True
    finally:
        db.close()


def test_refuses_to_rewrite_downstream_assessed_event(monkeypatch, failed_event):
    org_id, event_id, _ = failed_event
    db = SessionLocal()
    try:
        event = db.get(SemanticDriftEvent, event_id)
        event.risk_scored_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(
        recovery, "_process_semantic_analysis",
        lambda **kwargs: pytest.fail("Provider called for already assessed event"),
    )
    result = recovery.recover_event(event_id, organization_id=org_id)
    assert result.outcome == "downstream_conflict"
    db = SessionLocal()
    try:
        event = db.get(SemanticDriftEvent, event_id)
        assert event.analysis_status == "failed" and event.analysis_attempts == 1
    finally:
        db.close()


def test_operator_can_requalify_a_permanent_failed_event(monkeypatch, failed_event):
    org_id, event_id, _ = failed_event
    db = SessionLocal()
    try:
        event = db.get(SemanticDriftEvent, event_id)
        event.analysis_attempts = 3
        event.semantic_delta_json = {"analysis_failure": {"retryable": False}}
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(recovery, "_process_semantic_analysis", _provider_result)
    monkeypatch.setattr(recovery, "_enrich_relationships", lambda *args: None)
    assert recovery.recover_event(event_id, organization_id=org_id).outcome == "not_due"
    result = recovery.recover_event(event_id, organization_id=org_id, requalify=True)
    assert result.outcome == "completed" and result.attempts == 4
    db = SessionLocal()
    try:
        event = db.get(SemanticDriftEvent, event_id)
        assert event.analysis_status == "completed"
        assert event.semantic_delta_json["analysis_recovery"]["attempts_after_claim"] == 4
    finally:
        db.close()


def test_recovery_persists_provider_reported_usage_and_model(monkeypatch, failed_event):
    """The same event becomes completed with observable provider metering."""
    org_id, event_id, _ = failed_event

    def with_execution_metadata(*args, **kwargs):
        delta, summary = _provider_result()
        delta["provider_execution"] = {
            "requested_model": "deepseek-flash",
            "reported_model": "deepseek-v4.1-flash",
            "usage_reported": True,
            "total_tokens": 847,
        }
        return delta, summary

    monkeypatch.setattr(recovery, "_process_semantic_analysis", with_execution_metadata)
    monkeypatch.setattr(recovery, "_enrich_relationships", lambda *args: None)
    result = recovery.recover_event(event_id, organization_id=org_id)
    assert result.outcome == "completed"

    with SessionLocal() as db:
        event = db.get(SemanticDriftEvent, event_id)
        assert event.analysis_status == "completed"
        assert event.llm_tokens_used == 847
        assert event.semantic_delta_json["provider_execution"] == {
            "requested_model": "deepseek-flash",
            "reported_model": "deepseek-v4.1-flash",
            "usage_reported": True,
            "total_tokens": 847,
        }
        assert "provider_execution" not in event.semantic_delta_json["semantic_analysis"]
