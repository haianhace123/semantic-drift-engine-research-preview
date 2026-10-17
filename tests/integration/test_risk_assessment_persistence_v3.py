from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest

from src.core.database import SessionLocal
from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.risk_assessment_service import (
    RiskAssessmentDependencyError,
    persist_risk_assessment_v2,
    persist_risk_assessment_v3,
)
from src.services.risk_version import (
    CURRENT_RISK_VERSION,
    RISK_VERSION_V2,
    RISK_VERSION_V3,
)
from tests.integration.test_risk_assessment_persistence import (
    _cleanup,
    _make_entity,
    _make_event,
    _make_organization,
    _make_page,
    _make_relationship,
    _persist_default_risk,
    _prepare_topology,
)


def _persist_default_risk_v3(
    db,
    *,
    event,
):
    return persist_risk_assessment_v3(
        db,
        event=event,
        semantic_magnitude=0.8,
        legacy_impact=0.6,
        semantic_severity=0.7,
        page_context=0.5,
        confidence=0.9,
    )


def _make_baseline_event(
    db,
    *,
    detected_at,
    suffix,
):
    organization = _make_organization(db)
    entity_a = _make_entity(
        db,
        organization_id=organization.id,
        suffix=f"{suffix}-a",
    )
    entity_b = _make_entity(
        db,
        organization_id=organization.id,
        suffix=f"{suffix}-b",
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
        suffix=suffix,
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
    _prepare_topology(db, event=event)
    return organization, event


def test_v2_and_v3_persist_independently_for_same_event():
    db = SessionLocal()
    organization_id = None

    try:
        organization, event = _make_baseline_event(
            db,
            detected_at=datetime(
                2026, 9, 1, 10, 0,
                tzinfo=timezone.utc,
            ),
            suffix="version-isolation",
        )
        organization_id = organization.id

        v2 = _persist_default_risk(
            db,
            event=event,
        )
        db.commit()

        v3 = _persist_default_risk_v3(
            db,
            event=event,
        )
        db.commit()

        assert v2.created is True
        assert v3.created is True
        assert v2.risk_version == RISK_VERSION_V2
        assert v3.risk_version == RISK_VERSION_V3
        assert v2.assessment_id != v3.assessment_id

        rows = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == organization.id,
                RiskAssessment.semantic_drift_event_id
                == event.id,
            )
            .order_by(RiskAssessment.risk_version.asc())
            .all()
        )

        assert len(rows) == 2
        assert {
            row.risk_version
            for row in rows
        } == {
            RISK_VERSION_V2,
            RISK_VERSION_V3,
        }

        # Persistence alone must not promote the event-facing projection.
        db.refresh(event)
        assert event.risk_score is None
        assert event.risk_score_version is None

        assert CURRENT_RISK_VERSION == RISK_VERSION_V2

    finally:
        try:
            _cleanup(db, organization_id)
        finally:
            db.close()


def test_v3_changed_input_retry_returns_persisted_assessment():
    db = SessionLocal()
    organization_id = None

    try:
        organization, event = _make_baseline_event(
            db,
            detected_at=datetime(
                2026, 9, 1, 11, 0,
                tzinfo=timezone.utc,
            ),
            suffix="v3-idempotency",
        )
        organization_id = organization.id

        first = _persist_default_risk_v3(
            db,
            event=event,
        )
        db.commit()

        second = persist_risk_assessment_v3(
            db,
            event=event,
            semantic_magnitude=0.0,
            legacy_impact=0.0,
            semantic_severity=0.0,
            page_context=0.0,
            confidence=0.0,
        )
        db.commit()

        assert first.created is True
        assert second.created is False
        assert second.assessment_id == first.assessment_id
        assert second.risk_version == RISK_VERSION_V3
        assert second.score == first.score
        assert (
            second.topology_contribution
            == first.topology_contribution
        )

    finally:
        try:
            _cleanup(db, organization_id)
        finally:
            db.close()


def test_v3_baseline_topology_persists_zero_topology_risk():
    db = SessionLocal()
    organization_id = None

    try:
        organization, event = _make_baseline_event(
            db,
            detected_at=datetime(
                2026, 9, 1, 12, 0,
                tzinfo=timezone.utc,
            ),
            suffix="v3-baseline",
        )
        organization_id = organization.id

        result = _persist_default_risk_v3(
            db,
            event=event,
        )
        db.commit()

        assert result.risk_version == RISK_VERSION_V3
        assert result.has_topology_signal is True
        assert result.has_previous_topology_state is False
        assert result.topology_score == 0.0
        assert result.topology_contribution == 0.0
        assert result.score == pytest.approx(
            0.54,
            abs=1e-6,
        )

    finally:
        try:
            _cleanup(db, organization_id)
        finally:
            db.close()


def test_v3_missing_topology_dependency_does_not_persist():
    db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026, 9, 1, 13, 0,
            tzinfo=timezone.utc,
        )
        organization = _make_organization(db)
        organization_id = organization.id
        entity = _make_entity(
            db,
            organization_id=organization.id,
            suffix="v3-missing-topology",
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
            suffix="v3-missing-topology",
        )
        db.commit()

        with pytest.raises(
            RiskAssessmentDependencyError
        ):
            _persist_default_risk_v3(
                db,
                event=event,
            )

        db.rollback()

        rows = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == organization.id,
                RiskAssessment.semantic_drift_event_id
                == event.id,
                RiskAssessment.risk_version
                == RISK_VERSION_V3,
            )
            .all()
        )
        assert rows == []

    finally:
        try:
            _cleanup(db, organization_id)
        finally:
            db.close()


def test_v3_concurrent_persistence_creates_one_assessment():
    setup_db = SessionLocal()
    organization_id = None

    try:
        organization, event = _make_baseline_event(
            setup_db,
            detected_at=datetime(
                2026, 9, 1, 14, 0,
                tzinfo=timezone.utc,
            ),
            suffix="v3-concurrency",
        )
        organization_id = organization.id
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

                result = _persist_default_risk_v3(
                    db,
                    event=worker_event,
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

        assert sorted(
            result[0]
            for result in results
        ) == [False, True]

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
                    == RISK_VERSION_V3,
                )
                .all()
            )
            assert len(rows) == 1
            assert str(rows[0].id) in assessment_ids
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


def test_v2_retry_remains_version_scoped_after_v3_persistence():
    db = SessionLocal()
    organization_id = None

    try:
        organization, event = _make_baseline_event(
            db,
            detected_at=datetime(
                2026, 9, 1, 15, 0,
                tzinfo=timezone.utc,
            ),
            suffix="v2-after-v3",
        )
        organization_id = organization.id

        v2_first = _persist_default_risk(
            db,
            event=event,
        )
        db.commit()

        v3 = _persist_default_risk_v3(
            db,
            event=event,
        )
        db.commit()

        v2_second = persist_risk_assessment_v2(
            db,
            event=event,
            semantic_magnitude=0.0,
            legacy_impact=0.0,
            semantic_severity=0.0,
            page_context=0.0,
            confidence=0.0,
        )
        db.commit()

        assert v2_second.created is False
        assert (
            v2_second.assessment_id
            == v2_first.assessment_id
        )
        assert v2_second.risk_version == RISK_VERSION_V2
        assert v3.risk_version == RISK_VERSION_V3
        assert v3.assessment_id != v2_first.assessment_id

    finally:
        try:
            _cleanup(db, organization_id)
        finally:
            db.close()
