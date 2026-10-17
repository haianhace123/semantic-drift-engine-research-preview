from __future__ import annotations

from enum import Enum
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services.risk_assessment_service import (
    RiskAssessmentDependencyError,
    RiskAssessmentPersistenceResult,
)
from src.services.risk_scoring_v2_orchestrator import (
    RiskScoringV2NotReadyError,
    RiskScoringV2Service,
    RiskScoringV2ValidationError,
    build_risk_v2_inputs,
)


class PageType(Enum):
    DOCS = "docs"
    SLA = "sla"


def _event(
    *,
    vector_status="completed",
    analysis_status="completed",
    magnitude=0.5,
    impact=80,
    risk_level="high",
    confidence=0.9,
    page_type=PageType.DOCS,
    category=None,
):
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        organization_id=(
            "00000000-0000-0000-0000-000000000002"
        ),
        vector_analysis_status=vector_status,
        analysis_status=analysis_status,
        semantic_magnitude=magnitude,
        impact_score=impact,
        risk_level=risk_level,
        confidence=confidence,
        category=category,
        monitored_page=SimpleNamespace(
            page_type=page_type,
        ),
        risk_score=None,
        risk_score_version=None,
        risk_score_components=None,
        risk_scoring_status="pending",
        risk_scoring_error=None,
        risk_scoring_attempts=0,
        risk_scored_at=None,
    )


def test_build_inputs_normalizes_event_evidence():
    event = _event()

    result = build_risk_v2_inputs(
        event
    )

    assert result.semantic_magnitude == 0.5
    assert result.legacy_impact == 0.8
    assert result.semantic_severity == 1.0
    assert result.page_context == 0.5
    assert result.confidence == 0.9


def test_build_inputs_accepts_percentage_confidence():
    event = _event(
        confidence=85,
    )

    result = build_risk_v2_inputs(
        event
    )

    assert result.confidence == 0.85


def test_build_inputs_uses_stronger_context():
    event = _event(
        page_type=PageType.DOCS,
        category="security",
    )

    result = build_risk_v2_inputs(
        event
    )

    assert result.page_context == 1.0


def test_build_inputs_requires_vector_completion():
    event = _event(
        vector_status="pending",
    )

    with pytest.raises(
        RiskScoringV2NotReadyError
    ):
        build_risk_v2_inputs(
            event
        )


def test_build_inputs_requires_magnitude():
    event = _event(
        magnitude=None,
    )

    with pytest.raises(
        RiskScoringV2NotReadyError
    ):
        build_risk_v2_inputs(
            event
        )


def test_build_inputs_rejects_invalid_magnitude():
    event = _event(
        magnitude=1.5,
    )

    with pytest.raises(
        RiskScoringV2ValidationError
    ):
        build_risk_v2_inputs(
            event
        )


def test_build_inputs_rejects_invalid_impact():
    event = _event(
        impact=101,
    )

    with pytest.raises(
        RiskScoringV2ValidationError
    ):
        build_risk_v2_inputs(
            event
        )


def test_build_inputs_rejects_invalid_confidence():
    event = _event(
        confidence=101,
    )

    with pytest.raises(
        RiskScoringV2ValidationError
    ):
        build_risk_v2_inputs(
            event
        )


def test_missing_topology_becomes_not_ready(
    monkeypatch,
):
    event = _event()
    db = Mock()

    def fail(*args, **kwargs):
        raise RiskAssessmentDependencyError(
            "topology missing"
        )

    monkeypatch.setattr(
        (
            "src.services."
            "risk_scoring_v2_orchestrator."
            "persist_risk_assessment_v2"
        ),
        fail,
    )

    service = RiskScoringV2Service()

    with pytest.raises(
        RiskScoringV2NotReadyError
    ):
        service.score_event(
            db,
            event,
        )

    assert (
        event.risk_scoring_status
        == "pending"
    )
    assert event.risk_scoring_attempts == 0
    assert event.risk_score is None


def test_completed_assessment_projects_to_event(
    monkeypatch,
):
    event = _event()
    db = Mock()

    assessment = (
        RiskAssessmentPersistenceResult(
            assessment_id=(
                __import__("uuid").UUID(
                    "00000000-0000-0000-0000-000000000010"
                )
            ),
            organization_id=(
                __import__("uuid").UUID(
                    "00000000-0000-0000-0000-000000000002"
                )
            ),
            event_id=(
                __import__("uuid").UUID(
                    "00000000-0000-0000-0000-000000000001"
                )
            ),
            risk_version="deterministic_v2",
            assessed_at=(
                __import__("datetime")
                .datetime.now(
                    __import__("datetime")
                    .timezone.utc
                )
            ),
            created=True,
            score=0.6775,
            semantic_magnitude_contribution=0.15,
            legacy_impact_contribution=0.12,
            semantic_severity_contribution=0.10,
            page_context_contribution=0.05,
            confidence_contribution=0.09,
            topology_score=0.67,
            topology_contribution=0.1675,
            topology_shift_component=0.4,
            topology_centrality_component=0.15,
            topology_downstream_component=0.12,
            has_topology_signal=True,
            has_previous_topology_state=True,
        )
    )

    monkeypatch.setattr(
        (
            "src.services."
            "risk_scoring_v2_orchestrator."
            "persist_risk_assessment_v2"
        ),
        lambda *args, **kwargs: assessment,
    )

    service = RiskScoringV2Service()

    result = service.score_event(
        db,
        event,
    )

    assert result.score == 0.6775
    assert result.score_percent == 67.75
    assert (
        result.risk_version
        == "deterministic_v2"
    )

    assert event.risk_score == 67.75
    assert event.risk_score_version == "v2"
    assert (
        event.risk_scoring_status
        == "completed"
    )
    assert event.risk_scoring_attempts == 1
    assert event.risk_scoring_error is None
    assert event.risk_scored_at is not None

    assert (
        event.risk_score_components[
            "assessment_version"
        ]
        == "deterministic_v2"
    )

    assert (
        event.risk_score_components[
            "topology"
        ][
            "has_previous_state"
        ]
        is True
    )

    db.add.assert_called_once_with(
        event
    )

def test_build_inputs_rejects_failed_semantic_analysis_even_with_vectors():
    event = _event(analysis_status="failed", vector_status="completed")
    with pytest.raises(
        RiskScoringV2NotReadyError,
        match="completed semantic analysis",
    ):
        build_risk_v2_inputs(event)
