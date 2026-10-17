"""PostgreSQL acceptance for recoverable snapshot/event persistence gaps.

Uses local immutable files and a controlled semantic provider response only.
Never calls the public internet or the external semantic provider.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.core.database import SessionLocal
from src.models import Entity, MonitoredPage, Organization, PageSnapshot, SemanticDriftEvent
from src.services import semantic_analysis_recovery_service as recovery

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is disabled",
)


@pytest.fixture
def snapshot_gap(tmp_path):
    suffix = uuid.uuid4().hex
    org_a, org_b, entity_id, page_id = (uuid.uuid4() for _ in range(4))
    snapshots = []
    db = SessionLocal()
    try:
        for org_id, label in ((org_a, "a"), (org_b, "b")):
            db.add(Organization(
                id=org_id, name=f"Gap recovery {label}",
                slug=f"gap-recover-{label}-{suffix}",
                billing_email=f"{label}-{suffix}@example.invalid", is_active=True,
            ))
        db.flush()
        db.add(Entity(id=entity_id, organization_id=org_a,
                      name="Gap policy", domain=f"gap-{suffix}.example.invalid"))
        db.flush()
        db.add(MonitoredPage(id=page_id, organization_id=org_a, entity_id=entity_id,
                              url=f"https://gap-{suffix}.example.invalid/policy",
                              page_type="docs", is_active=True))
        db.flush()
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        for index, text in enumerate((
            "Refunds are permitted for thirty days.",
            "Refunds are permitted for fourteen days.",
            "Refunds are permitted for seven days.",
        )):
            path = tmp_path / f"snapshot_{index}.txt"
            path.write_text(text, encoding="utf-8")
            snapshot = PageSnapshot(
                id=uuid.uuid4(), organization_id=org_a,
                monitored_page_id=page_id,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                raw_content_path=str(path),
                crawled_at=base + timedelta(seconds=index),
            )
            snapshots.append(snapshot.id)
            db.add(snapshot)
        db.commit()
        yield org_a, org_b, page_id, snapshots
    finally:
        db.rollback()
        db.query(SemanticDriftEvent).filter(SemanticDriftEvent.organization_id == org_a).delete(synchronize_session=False)
        db.query(PageSnapshot).filter(PageSnapshot.id.in_(snapshots)).delete(synchronize_session=False)
        db.query(MonitoredPage).filter(MonitoredPage.id == page_id).delete(synchronize_session=False)
        db.query(Entity).filter(Entity.id == entity_id).delete(synchronize_session=False)
        db.query(Organization).filter(Organization.id.in_([org_a, org_b])).delete(synchronize_session=False)
        db.commit()
        db.close()


def _validated_provider_response(*args, **kwargs):
    return ({
        "chunks_analyzed": 1,
        "semantic_analysis": {
            "analysis_status": "success",
            "changes": [{
                "category": "policy", "old_meaning": "Thirty days",
                "new_meaning": "Seven days", "impact": "Refund window shortened",
                "risk_level": "high",
            }],
            "summary": "Refund policy changed", "overall_risk": "high",
        },
    }, "Refund policy changed")


def test_registers_older_and_latest_missing_pairs_then_recovers_in_place(monkeypatch, snapshot_gap):
    org_a, org_b, page_id, snap_ids = snapshot_gap
    assert not recovery.missing_snapshot_pair_candidates(organization_id=org_b)
    missing = recovery.missing_snapshot_pair_candidates(
        organization_id=org_a, limit=5, scan_limit=20,
    )
    page_pairs = [p for p in missing if p.monitored_page_id == page_id]
    assert [(p.old_snapshot_id, p.new_snapshot_id) for p in page_pairs] == [
        (snap_ids[0], snap_ids[1]), (snap_ids[1], snap_ids[2]),
    ]
    ids = [recovery.register_missing_snapshot_pair(p) for p in page_pairs]
    assert all(ids) and len(set(ids)) == 2
    assert all(recovery.register_missing_snapshot_pair(p) is None for p in page_pairs)
    assert not [p for p in recovery.missing_snapshot_pair_candidates(
        organization_id=org_a, limit=5, scan_limit=20,
    ) if p.monitored_page_id == page_id]
    with SessionLocal() as db:
        for event_id in ids:
            event = db.get(SemanticDriftEvent, event_id)
            assert event.analysis_status == "pending"
            assert event.analysis_attempts == 0
            assert event.risk_level == "unknown"
            assert event.semantic_delta_json["analysis_recovery"]["source"] == "snapshot_gap"
    candidates = recovery.recovery_candidates(organization_id=org_a, limit=100)
    assert set(ids).issubset(set(candidates))
    monkeypatch.setattr(recovery, "_process_semantic_analysis", _validated_provider_response)
    monkeypatch.setattr(recovery, "_enrich_relationships", lambda *args: None)
    for event_id in ids:
        assert recovery.recover_event(event_id, organization_id=org_b).outcome == "not_found"
        result = recovery.recover_event(event_id, organization_id=org_a)
        assert result.outcome == "completed" and result.attempts == 1
        assert recovery.recover_event(event_id, organization_id=org_a).outcome == "not_due"
    with SessionLocal() as db:
        assert db.query(SemanticDriftEvent).filter(
            SemanticDriftEvent.organization_id == org_a,
            SemanticDriftEvent.monitored_page_id == page_id,
        ).count() == 2
        for event_id in ids:
            event = db.get(SemanticDriftEvent, event_id)
            assert event.analysis_status == "completed"
            assert event.semantic_delta_json["analysis_recovery"]["source"] == "snapshot_gap"


def test_registration_rechecks_adjacency_and_does_not_cross_tenants(snapshot_gap):
    org_a, org_b, page_id, ids = snapshot_gap
    pair = recovery.MissingSnapshotPair(org_a, page_id, ids[0], ids[2])
    assert recovery.register_missing_snapshot_pair(pair) is None  # middle snapshot exists
    assert recovery.register_missing_snapshot_pair(
        recovery.MissingSnapshotPair(org_b, page_id, ids[0], ids[1])
    ) is None
    with SessionLocal() as db:
        assert db.query(SemanticDriftEvent).filter(
            SemanticDriftEvent.monitored_page_id == page_id,
        ).count() == 0


def test_unchanged_snapshots_do_not_create_pending_events(snapshot_gap):
    org_a, _, page_id, ids = snapshot_gap
    with SessionLocal() as db:
        first = db.get(PageSnapshot, ids[0])
        second = db.get(PageSnapshot, ids[1])
        second.content_hash = first.content_hash
        db.commit()
    pairs = recovery.missing_snapshot_pair_candidates(
        organization_id=org_a, limit=5, scan_limit=20,
    )
    assert (ids[0], ids[1]) not in [(p.old_snapshot_id, p.new_snapshot_id) for p in pairs]
    assert recovery.register_missing_snapshot_pair(
        recovery.MissingSnapshotPair(org_a, page_id, ids[0], ids[1])
    ) is None


def test_two_registrars_create_one_durable_event(snapshot_gap):
    from concurrent.futures import ThreadPoolExecutor

    org_a, _, page_id, ids = snapshot_gap
    pair = recovery.MissingSnapshotPair(org_a, page_id, ids[0], ids[1])
    with ThreadPoolExecutor(max_workers=2) as pool:
        created = list(pool.map(recovery.register_missing_snapshot_pair, [pair, pair]))
    assert len([event_id for event_id in created if event_id is not None]) == 1
    with SessionLocal() as db:
        assert db.query(SemanticDriftEvent).filter(
            SemanticDriftEvent.old_snapshot_id == ids[0],
            SemanticDriftEvent.new_snapshot_id == ids[1],
        ).count() == 1


def test_crash_after_durable_attempt_respects_retry_backoff(monkeypatch, snapshot_gap):
    org_a, _, page_id, ids = snapshot_gap
    pair = recovery.MissingSnapshotPair(org_a, page_id, ids[0], ids[1])
    event_id = recovery.register_missing_snapshot_pair(pair)
    assert event_id is not None
    with SessionLocal() as db:
        event = db.get(SemanticDriftEvent, event_id)
        event.analysis_attempts = 1
        event.analyzed_at = datetime.now(timezone.utc)
        db.commit()  # Simulate provider-process death after reserved attempt.
    monkeypatch.setattr(recovery, "_process_semantic_analysis",
                        lambda **kw: pytest.fail("Called provider during cooldown"))
    assert recovery.recover_event(event_id, organization_id=org_a).outcome == "not_due"
    with SessionLocal() as db:
        event = db.get(SemanticDriftEvent, event_id)
        event.analyzed_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
    monkeypatch.setattr(recovery, "_process_semantic_analysis", _validated_provider_response)
    monkeypatch.setattr(recovery, "_enrich_relationships", lambda *args: None)
    result = recovery.recover_event(event_id, organization_id=org_a)
    assert result.outcome == "completed" and result.attempts == 2
    with SessionLocal() as db:
        assert db.get(SemanticDriftEvent, event_id).analysis_status == "completed"
