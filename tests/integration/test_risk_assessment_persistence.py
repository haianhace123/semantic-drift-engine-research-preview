from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.models.entity import Entity
from src.models.entity_relationship import EntityRelationship
from src.models.entity_relationship_observation import (
    EntityRelationshipObservation,
)
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.graph_metric_snapshot_service import (
    persist_graph_metric_snapshots_for_event,
)
from src.services.risk_assessment_service import (
    RISK_VERSION_V2,
    RiskAssessmentDependencyError,
    persist_risk_assessment_v2,
)
from src.services.topology_shift_snapshot_service import (
    persist_topology_shift_snapshots_for_event,
)


def _make_organization(db):
    suffix = uuid.uuid4().hex[:10]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"risk-assessment-{suffix}",
        slug=f"risk-assessment-{suffix}",
        billing_email=f"{suffix}@example.com",
    )

    db.add(organization)
    db.flush()

    return organization


def _make_entity(
    db,
    *,
    organization_id,
    suffix,
):
    entity = Entity(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name=f"Risk Assessment {suffix}",
        domain=(
            f"{suffix}-"
            f"{uuid.uuid4().hex[:8]}"
            ".example.com"
        ),
        category="test",
    )

    db.add(entity)
    db.flush()

    return entity


def _make_page(
    db,
    *,
    organization_id,
    entity_id,
):
    suffix = uuid.uuid4().hex[:8]

    page = MonitoredPage(
        id=uuid.uuid4(),
        organization_id=organization_id,
        entity_id=entity_id,
        url=f"https://{suffix}.example.com/docs",
        page_type="docs",
        crawl_frequency="hourly",
        is_active=True,
    )

    db.add(page)
    db.flush()

    return page


def _make_snapshot(
    db,
    *,
    organization_id,
    monitored_page_id,
    suffix,
):
    raw_content = f"risk assessment {suffix}"

    snapshot = PageSnapshot(
        id=uuid.uuid4(),
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        content_hash=(
            f"{suffix}-{uuid.uuid4().hex}"
        ),
        raw_content=raw_content,
        content_size=len(raw_content),
    )

    db.add(snapshot)
    db.flush()

    return snapshot


def _make_event(
    db,
    *,
    organization_id,
    monitored_page_id,
    detected_at,
    suffix,
):
    old_snapshot = _make_snapshot(
        db,
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        suffix=f"{suffix}-old",
    )

    new_snapshot = _make_snapshot(
        db,
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        suffix=f"{suffix}-new",
    )

    event = SemanticDriftEvent(
        id=uuid.uuid4(),
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        old_snapshot_id=old_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        semantic_delta_json={},
        impact_score=0,
        entities_affected=[],
        related_events=[],
        detected_at=detected_at,
    )

    db.add(event)
    db.flush()

    return event


def _make_relationship(
    db,
    *,
    organization_id,
    source_entity_id,
    target_entity_id,
    event_id,
    observed_at,
):
    relationship = EntityRelationship(
        id=uuid.uuid4(),
        organization_id=organization_id,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relationship_type="mentioned",
        evidence_method="risk_assessment_test",
        evidence_count=1,
        strength=1.0,
        confidence=1.0,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        first_event_id=event_id,
        last_event_id=event_id,
        is_active=True,
    )

    db.add(relationship)
    db.flush()

    observation = EntityRelationshipObservation(
        id=uuid.uuid4(),
        organization_id=organization_id,
        relationship_id=relationship.id,
        semantic_drift_event_id=event_id,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relationship_type="mentioned",
        evidence_method="risk_assessment_test",
        observed_at=observed_at,
    )

    db.add(observation)
    db.flush()

    return relationship


def _prepare_topology(
    db,
    *,
    event,
):
    persist_graph_metric_snapshots_for_event(
        db=db,
        event=event,
    )
    db.commit()

    persist_topology_shift_snapshots_for_event(
        db=db,
        event=event,
    )
    db.commit()


def _persist_default_risk(
    db,
    *,
    event,
):
    return persist_risk_assessment_v2(
        db,
        event=event,
        semantic_magnitude=0.8,
        legacy_impact=0.6,
        semantic_severity=0.7,
        page_context=0.5,
        confidence=0.9,
    )


def _cleanup(
    db,
    organization_id,
):
    if organization_id is None:
        return

    db.rollback()

    # RiskAssessment intentionally survives SemanticDriftEvent deletion
    # because its event FK uses ON DELETE SET NULL. Remove assessments
    # explicitly before deleting the test organization so integration
    # tests never leak orphaned risk rows into the shared dev database.
    db.execute(
        delete(RiskAssessment).where(
            RiskAssessment.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(Organization).where(
            Organization.id
            == organization_id
        )
    )

    db.commit()


def test_persists_risk_assessment_and_retry_is_idempotent():
    db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026,
            8,
            30,
            13,
            0,
            tzinfo=timezone.utc,
        )

        organization = _make_organization(db)
        organization_id = organization.id

        entity_a = _make_entity(
            db,
            organization_id=organization.id,
            suffix="a",
        )

        entity_b = _make_entity(
            db,
            organization_id=organization.id,
            suffix="b",
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity_a.id,
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=detected_at,
            suffix="normal",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=entity_a.id,
            target_entity_id=entity_b.id,
            event_id=event.id,
            observed_at=detected_at,
        )

        db.commit()

        _prepare_topology(
            db,
            event=event,
        )

        first = _persist_default_risk(
            db,
            event=event,
        )
        db.commit()

        second = _persist_default_risk(
            db,
            event=event,
        )
        db.commit()

        assert first.created is True
        assert second.created is False

        assert (
            first.assessment_id
            == second.assessment_id
        )

        rows = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == organization.id,
                RiskAssessment.semantic_drift_event_id
                == event.id,
                RiskAssessment.risk_version
                == RISK_VERSION_V2,
            )
            .all()
        )

        assert len(rows) == 1

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_changed_input_retry_returns_persisted_assessment():
    db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026,
            8,
            30,
            14,
            0,
            tzinfo=timezone.utc,
        )

        organization = _make_organization(db)
        organization_id = organization.id

        entity_a = _make_entity(
            db,
            organization_id=organization.id,
            suffix="a",
        )

        entity_b = _make_entity(
            db,
            organization_id=organization.id,
            suffix="b",
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity_a.id,
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=detected_at,
            suffix="changed-input",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=entity_a.id,
            target_entity_id=entity_b.id,
            event_id=event.id,
            observed_at=detected_at,
        )

        db.commit()

        _prepare_topology(
            db,
            event=event,
        )

        first = _persist_default_risk(
            db,
            event=event,
        )
        db.commit()

        second = persist_risk_assessment_v2(
            db,
            event=event,
            semantic_magnitude=0.0,
            legacy_impact=0.0,
            semantic_severity=0.0,
            page_context=0.0,
            confidence=0.0,
        )
        db.commit()

        assert second.created is False

        assert (
            second.assessment_id
            == first.assessment_id
        )

        assert second.score == first.score

        assert (
            second.topology_contribution
            == first.topology_contribution
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_baseline_topology_persists_zero_topology_risk():
    db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026,
            8,
            30,
            15,
            0,
            tzinfo=timezone.utc,
        )

        organization = _make_organization(db)
        organization_id = organization.id

        entity_a = _make_entity(
            db,
            organization_id=organization.id,
            suffix="a",
        )

        entity_b = _make_entity(
            db,
            organization_id=organization.id,
            suffix="b",
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity_a.id,
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=detected_at,
            suffix="baseline",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=entity_a.id,
            target_entity_id=entity_b.id,
            event_id=event.id,
            observed_at=detected_at,
        )

        db.commit()

        _prepare_topology(
            db,
            event=event,
        )

        result = _persist_default_risk(
            db,
            event=event,
        )
        db.commit()

        assert (
            result.has_previous_topology_state
            is False
        )

        assert result.topology_score == 0.0

        assert (
            result.topology_contribution
            == 0.0
        )

        assert result.score == pytest.approx(
            0.54,
            abs=1e-6,
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_missing_topology_dependency_does_not_persist():
    db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026,
            8,
            30,
            16,
            0,
            tzinfo=timezone.utc,
        )

        organization = _make_organization(db)
        organization_id = organization.id

        entity = _make_entity(
            db,
            organization_id=organization.id,
            suffix="a",
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=detected_at,
            suffix="missing-topology",
        )

        db.commit()

        with pytest.raises(
            RiskAssessmentDependencyError
        ):
            _persist_default_risk(
                db,
                event=event,
            )

        db.rollback()

        count = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == organization.id,
                RiskAssessment.semantic_drift_event_id
                == event.id,
            )
            .count()
        )

        assert count == 0

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_event_deletion_preserves_assessment_with_null_event():
    db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026,
            8,
            30,
            17,
            0,
            tzinfo=timezone.utc,
        )

        organization = _make_organization(db)
        organization_id = organization.id

        entity_a = _make_entity(
            db,
            organization_id=organization.id,
            suffix="a",
        )

        entity_b = _make_entity(
            db,
            organization_id=organization.id,
            suffix="b",
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity_a.id,
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=detected_at,
            suffix="lifecycle",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=entity_a.id,
            target_entity_id=entity_b.id,
            event_id=event.id,
            observed_at=detected_at,
        )

        db.commit()

        _prepare_topology(
            db,
            event=event,
        )

        result = _persist_default_risk(
            db,
            event=event,
        )
        db.commit()

        assessment_id = result.assessment_id
        event_id = event.id

        db.execute(
            delete(SemanticDriftEvent).where(
                SemanticDriftEvent.id == event_id
            )
        )
        db.commit()

        assessment = db.get(
            RiskAssessment,
            assessment_id,
        )

        assert assessment is not None

        assert (
            assessment.semantic_drift_event_id
            is None
        )

        assert (
            assessment.organization_id
            == organization.id
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_concurrent_persistence_creates_one_assessment():
    setup_db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026,
            8,
            30,
            18,
            0,
            tzinfo=timezone.utc,
        )

        organization = _make_organization(
            setup_db
        )
        organization_id = organization.id

        entity_a = _make_entity(
            setup_db,
            organization_id=organization.id,
            suffix="a",
        )

        entity_b = _make_entity(
            setup_db,
            organization_id=organization.id,
            suffix="b",
        )

        page = _make_page(
            setup_db,
            organization_id=organization.id,
            entity_id=entity_a.id,
        )

        event = _make_event(
            setup_db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=detected_at,
            suffix="concurrency",
        )

        _make_relationship(
            setup_db,
            organization_id=organization.id,
            source_entity_id=entity_a.id,
            target_entity_id=entity_b.id,
            event_id=event.id,
            observed_at=detected_at,
        )

        setup_db.commit()

        _prepare_topology(
            setup_db,
            event=event,
        )

        event_id = event.id

        barrier = Barrier(2)

        def worker():
            db = SessionLocal()

            try:
                worker_event = db.get(
                    SemanticDriftEvent,
                    event_id,
                )

                assert worker_event is not None

                barrier.wait()

                result = (
                    persist_risk_assessment_v2(
                        db,
                        event=worker_event,
                        semantic_magnitude=0.8,
                        legacy_impact=0.6,
                        semantic_severity=0.7,
                        page_context=0.5,
                        confidence=0.9,
                    )
                )

                db.commit()

                return (
                    result.created,
                    str(result.assessment_id),
                )

            finally:
                db.close()

        with ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            results = list(
                executor.map(
                    lambda _: worker(),
                    range(2),
                )
            )

        created_flags = sorted(
            result[0]
            for result in results
        )

        assert created_flags == [
            False,
            True,
        ]

        assessment_ids = {
            result[1]
            for result in results
        }

        assert len(assessment_ids) == 1

        verify_db = SessionLocal()

        try:
            rows = (
                verify_db.query(RiskAssessment)
                .filter(
                    RiskAssessment.organization_id
                    == organization_id,
                    RiskAssessment
                    .semantic_drift_event_id
                    == event_id,
                    RiskAssessment.risk_version
                    == RISK_VERSION_V2,
                )
                .all()
            )

            assert len(rows) == 1

            assert (
                str(rows[0].id)
                in assessment_ids
            )

        finally:
            verify_db.close()

    finally:
        try:
            _cleanup(
                setup_db,
                organization_id,
            )
        finally:
            setup_db.close()