from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from src.core.database import SessionLocal
from src.models.entity_graph_metric_snapshot import (
    EntityGraphMetricSnapshot,
)
from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
    VECTOR_ANALYSIS_STATUS_COMPLETED,
)
from src.models.topology_shift_snapshot import (
    TopologyShiftSnapshot,
)
from src.services.graph_service import persist_event_relationships
from src.services.risk_v3_requalification_service import (
    requalify_risk_v3,
)
from tests.integration.test_risk_scoring_v2 import (
    _cleanup,
    _make_entity,
    _make_event,
    _make_organization,
    _make_page,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="Database integration tests are disabled.",
)


_EVENT_PROJECTION_FIELDS = (
    "risk_level",
    "risk_score",
    "risk_score_version",
    "risk_score_components",
    "risk_scoring_status",
    "risk_scoring_error",
    "risk_scoring_attempts",
    "risk_scored_at",
)


def _prepare_eligible_event(
    event: SemanticDriftEvent,
    *,
    semantic_magnitude: float,
    impact_score: int,
    confidence: float,
    risk_level: str,
) -> None:
    event.vector_analysis_status = (
        VECTOR_ANALYSIS_STATUS_COMPLETED
    )
    event.semantic_similarity = 1.0 - semantic_magnitude
    event.semantic_distance = semantic_magnitude
    event.semantic_magnitude = semantic_magnitude
    event.impact_score = impact_score
    event.confidence = confidence
    event.risk_level = risk_level


def _event_projection(
    event: SemanticDriftEvent,
) -> tuple:
    return tuple(
        getattr(event, field)
        for field in _EVENT_PROJECTION_FIELDS
    )


def _evidence_counts(
    db,
    *,
    organization_id: uuid.UUID,
) -> tuple[int, int, int]:
    return (
        db.query(EntityGraphMetricSnapshot)
        .filter(
            EntityGraphMetricSnapshot.organization_id
            == organization_id
        )
        .count(),
        db.query(TopologyShiftSnapshot)
        .filter(
            TopologyShiftSnapshot.organization_id
            == organization_id
        )
        .count(),
        db.query(RiskAssessment)
        .filter(
            RiskAssessment.organization_id
            == organization_id
        )
        .count(),
    )


def test_requalification_rebuilds_from_observations_without_persistence():
    setup_db = SessionLocal()
    organization_id = None

    try:
        t1 = datetime(
            2026,
            9,
            10,
            8,
            0,
            tzinfo=timezone.utc,
        )
        t2 = datetime(
            2026,
            9,
            10,
            9,
            0,
            tzinfo=timezone.utc,
        )

        suffix = uuid.uuid4().hex[:8]

        organization = _make_organization(
            setup_db,
            suffix=suffix,
        )
        organization_id = organization.id

        entity_a = _make_entity(
            setup_db,
            organization_id=organization_id,
            suffix=f"{suffix}-a",
        )
        entity_b = _make_entity(
            setup_db,
            organization_id=organization_id,
            suffix=f"{suffix}-b",
        )
        entity_c = _make_entity(
            setup_db,
            organization_id=organization_id,
            suffix=f"{suffix}-c",
        )

        page = _make_page(
            setup_db,
            organization_id=organization_id,
            entity_id=entity_a.id,
            suffix=suffix,
        )

        event_t1 = _make_event(
            setup_db,
            organization_id=organization_id,
            monitored_page_id=page.id,
            detected_at=t1,
            suffix=f"{suffix}-t1",
        )
        event_t2 = _make_event(
            setup_db,
            organization_id=organization_id,
            monitored_page_id=page.id,
            detected_at=t2,
            suffix=f"{suffix}-t2",
        )

        _prepare_eligible_event(
            event_t1,
            semantic_magnitude=0.5,
            impact_score=60,
            confidence=0.8,
            risk_level="medium",
        )
        _prepare_eligible_event(
            event_t2,
            semantic_magnitude=0.7,
            impact_score=80,
            confidence=0.9,
            risk_level="high",
        )

        event_t1.entities_affected = [
            str(entity_b.id),
        ]
        event_t2.entities_affected = [
            str(entity_c.id),
        ]
        setup_db.flush()

        first_graph_result = persist_event_relationships(
            db=setup_db,
            event=event_t1,
            source_entity_id=entity_a.id,
        )
        second_graph_result = persist_event_relationships(
            db=setup_db,
            event=event_t2,
            source_entity_id=entity_b.id,
        )

        assert first_graph_result.created == 1
        assert first_graph_result.updated == 0
        assert first_graph_result.observations_created == 1
        assert second_graph_result.created == 1
        assert second_graph_result.updated == 0
        assert second_graph_result.observations_created == 1

        setup_db.commit()

        event_ids = (
            event_t1.id,
            event_t2.id,
        )

        setup_db.expire_all()

        persisted_events = (
            setup_db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id.in_(event_ids)
            )
            .order_by(
                SemanticDriftEvent.detected_at.asc()
            )
            .all()
        )

        projection_before = {
            event.id: _event_projection(event)
            for event in persisted_events
        }

        counts_before = _evidence_counts(
            setup_db,
            organization_id=organization_id,
        )

        setup_db.close()

        db = SessionLocal()

        try:
            assert not db.new
            assert not db.dirty
            assert not db.deleted

            result = requalify_risk_v3(
                db,
                organization_id=organization_id,
            )

            assert result.organization_id == organization_id
            assert result.eligible_event_count == 2
            assert result.paired_event_count == 2
            assert result.invalid_event_count == 0
            assert result.distinct_timestamp_count == 2
            assert len(result.comparisons) == 2
            assert result.evaluation is not None
            assert result.evaluation.paired_count == 2
            assert (
                result.evaluation.non_topology_contribution_mismatches
                == 0
            )
            assert (
                result.qualification_evidence.previous_state_pairs
                == 1
            )
            assert (
                result.qualification_evidence.exercised_topology_pairs
                > 0
            )
            assert (
                result.qualification_evidence.topology_exercise_observed
                is True
            )

            comparisons = {
                comparison.event_id: comparison
                for comparison in result.comparisons
            }

            assert set(comparisons) == set(event_ids)

            assert (
                comparisons[event_t1.id]
                .topology
                .baseline_has_previous_state
                is False
            )
            assert (
                comparisons[event_t1.id]
                .topology
                .candidate_has_previous_state
                is False
            )

            assert (
                comparisons[event_t2.id]
                .topology
                .baseline_has_previous_state
                is True
            )
            assert (
                comparisons[event_t2.id]
                .topology
                .candidate_has_previous_state
                is True
            )

            assert not db.new
            assert not db.dirty
            assert not db.deleted

            counts_after = _evidence_counts(
                db,
                organization_id=organization_id,
            )
            assert counts_after == counts_before

            db.expire_all()

            events_after = (
                db.query(SemanticDriftEvent)
                .filter(
                    SemanticDriftEvent.id.in_(event_ids)
                )
                .all()
            )

            projection_after = {
                event.id: _event_projection(event)
                for event in events_after
            }

            assert projection_after == projection_before

            assert not db.new
            assert not db.dirty
            assert not db.deleted

        finally:
            db.close()

    finally:
        if setup_db.is_active:
            try:
                _cleanup(
                    setup_db,
                    organization_id,
                )
            finally:
                setup_db.close()
        else:
            setup_db.close()
