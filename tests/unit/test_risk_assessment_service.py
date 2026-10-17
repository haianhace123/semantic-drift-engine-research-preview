from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services.risk_assessment_service import (
    RISK_VERSION_V2,
    RiskAssessmentDependencyError,
    persist_risk_assessment_v2,
)
from src.services.risk_scoring_v2_service import (
    TopologyRiskInput,
)


def _event():
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        detected_at=datetime(
            2026,
            8,
            30,
            12,
            0,
            tzinfo=timezone.utc,
        ),
    )


def _topology_signal(
    *,
    has_previous_state=True,
):
    return SimpleNamespace(
        risk_input=TopologyRiskInput(
            shift_score=0.8,
            degree_centrality_delta=0.4,
            downstream_reach_ratio_delta=0.2,
            has_previous_state=has_previous_state,
        )
    )


def test_requires_persisted_topology_before_first_assessment():
    db = MagicMock()
    event = _event()

    with (
        patch(
            "src.services.risk_assessment_service."
            "_load_existing_assessment",
            return_value=None,
        ),
        patch(
            "src.services.risk_assessment_service."
            "load_event_topology_risk_signal",
            return_value=None,
        ),
    ):
        with pytest.raises(
            RiskAssessmentDependencyError,
            match="requires persisted topology",
        ):
            persist_risk_assessment_v2(
                db,
                event=event,
                semantic_magnitude=0.8,
                legacy_impact=0.6,
                semantic_severity=0.7,
                page_context=0.5,
                confidence=0.9,
            )


def test_baseline_topology_can_be_persisted():
    db = MagicMock()
    event = _event()
    assessment_id = uuid.uuid4()

    with (
        patch(
            "src.services.risk_assessment_service."
            "_load_existing_assessment",
            return_value=None,
        ),
        patch(
            "src.services.risk_assessment_service."
            "load_event_topology_risk_signal",
            return_value=_topology_signal(
                has_previous_state=False,
            ),
        ),
        patch(
            "src.services.risk_assessment_service."
            "_insert_assessment",
            return_value=assessment_id,
        ),
    ):
        result = persist_risk_assessment_v2(
            db,
            event=event,
            semantic_magnitude=0.8,
            legacy_impact=0.6,
            semantic_severity=0.7,
            page_context=0.5,
            confidence=0.9,
        )

    assert result.created is True
    assert result.risk_version == RISK_VERSION_V2
    assert result.assessment_id == assessment_id

    assert (
        result.has_previous_topology_state
        is False
    )

    assert result.topology_score == 0.0
    assert result.topology_contribution == 0.0
    assert result.score == pytest.approx(
        0.54,
        abs=1e-6,
    )


def test_previous_topology_contributes_to_persisted_risk():
    db = MagicMock()
    event = _event()

    with (
        patch(
            "src.services.risk_assessment_service."
            "_load_existing_assessment",
            return_value=None,
        ),
        patch(
            "src.services.risk_assessment_service."
            "load_event_topology_risk_signal",
            return_value=_topology_signal(
                has_previous_state=True,
            ),
        ),
        patch(
            "src.services.risk_assessment_service."
            "_insert_assessment",
            return_value=uuid.uuid4(),
        ),
    ):
        result = persist_risk_assessment_v2(
            db,
            event=event,
            semantic_magnitude=0.8,
            legacy_impact=0.6,
            semantic_severity=0.7,
            page_context=0.5,
            confidence=0.9,
        )

    assert result.created is True
    assert result.has_topology_signal is True

    assert (
        result.has_previous_topology_state
        is True
    )

    assert result.topology_score > 0.0
    assert result.topology_contribution > 0.0
    assert result.score > 0.54


def test_existing_assessment_short_circuits_recalculation():
    db = MagicMock()
    event = _event()
    assessment_id = uuid.uuid4()

    existing = SimpleNamespace(
        id=assessment_id,
        organization_id=event.organization_id,
        semantic_drift_event_id=event.id,
        risk_version=RISK_VERSION_V2,
        assessed_at=event.detected_at,
        score=0.61,
        semantic_magnitude_contribution=0.2,
        legacy_impact_contribution=0.1,
        semantic_severity_contribution=0.08,
        page_context_contribution=0.05,
        confidence_contribution=0.08,
        topology_score=0.4,
        topology_contribution=0.1,
        topology_shift_component=0.5,
        topology_centrality_component=0.3,
        topology_downstream_component=0.2,
        has_topology_signal=True,
        has_previous_topology_state=True,
    )

    with (
        patch(
            "src.services.risk_assessment_service."
            "_load_existing_assessment",
            return_value=existing,
        ),
        patch(
            "src.services.risk_assessment_service."
            "load_event_topology_risk_signal",
        ) as topology_loader,
        patch(
            "src.services.risk_assessment_service."
            "_insert_assessment",
        ) as inserter,
    ):
        result = persist_risk_assessment_v2(
            db,
            event=event,
            semantic_magnitude=0.0,
            legacy_impact=0.0,
            semantic_severity=0.0,
            page_context=0.0,
            confidence=0.0,
        )

    topology_loader.assert_not_called()
    inserter.assert_not_called()

    assert result.created is False
    assert result.assessment_id == assessment_id
    assert result.score == 0.61
    assert result.topology_score == 0.4


def test_conflict_returns_persisted_winner():
    db = MagicMock()
    event = _event()
    winner_id = uuid.uuid4()

    winner = SimpleNamespace(
        id=winner_id,
        organization_id=event.organization_id,
        semantic_drift_event_id=event.id,
        risk_version=RISK_VERSION_V2,
        assessed_at=event.detected_at,
        score=0.72,
        semantic_magnitude_contribution=0.24,
        legacy_impact_contribution=0.09,
        semantic_severity_contribution=0.07,
        page_context_contribution=0.05,
        confidence_contribution=0.09,
        topology_score=0.68,
        topology_contribution=0.17,
        topology_shift_component=0.8,
        topology_centrality_component=0.6,
        topology_downstream_component=0.4,
        has_topology_signal=True,
        has_previous_topology_state=True,
    )

    with (
        patch(
            "src.services.risk_assessment_service."
            "_load_existing_assessment",
            side_effect=[
                None,
                winner,
            ],
        ),
        patch(
            "src.services.risk_assessment_service."
            "load_event_topology_risk_signal",
            return_value=_topology_signal(),
        ),
        patch(
            "src.services.risk_assessment_service."
            "_insert_assessment",
            return_value=None,
        ),
    ):
        result = persist_risk_assessment_v2(
            db,
            event=event,
            semantic_magnitude=0.1,
            legacy_impact=0.1,
            semantic_severity=0.1,
            page_context=0.1,
            confidence=0.1,
        )

    assert result.created is False
    assert result.assessment_id == winner_id
    assert result.score == 0.72
    assert result.topology_contribution == 0.17


def test_conflict_without_visible_winner_is_integrity_error():
    db = MagicMock()
    event = _event()

    with (
        patch(
            "src.services.risk_assessment_service."
            "_load_existing_assessment",
            side_effect=[
                None,
                None,
            ],
        ),
        patch(
            "src.services.risk_assessment_service."
            "load_event_topology_risk_signal",
            return_value=_topology_signal(),
        ),
        patch(
            "src.services.risk_assessment_service."
            "_insert_assessment",
            return_value=None,
        ),
    ):
        with pytest.raises(
            RuntimeError,
            match="could not be loaded",
        ):
            persist_risk_assessment_v2(
                db,
                event=event,
                semantic_magnitude=0.8,
                legacy_impact=0.6,
                semantic_severity=0.7,
                page_context=0.5,
                confidence=0.9,
            )


def test_requires_event_id():
    db = MagicMock()

    event = SimpleNamespace(
        id=None,
        organization_id=uuid.uuid4(),
        detected_at=datetime.now(
            timezone.utc
        ),
    )

    with pytest.raises(
        ValueError,
        match="event.id is required",
    ):
        persist_risk_assessment_v2(
            db,
            event=event,
            semantic_magnitude=0.0,
            legacy_impact=0.0,
            semantic_severity=0.0,
            page_context=0.0,
            confidence=0.0,
        )


def test_requires_event_organization():
    db = MagicMock()

    event = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=None,
        detected_at=datetime.now(
            timezone.utc
        ),
    )

    with pytest.raises(
        ValueError,
        match="event.organization_id is required",
    ):
        persist_risk_assessment_v2(
            db,
            event=event,
            semantic_magnitude=0.0,
            legacy_impact=0.0,
            semantic_severity=0.0,
            page_context=0.0,
            confidence=0.0,
        )


def test_requires_event_detected_at():
    db = MagicMock()

    event = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        detected_at=None,
    )

    with pytest.raises(
        ValueError,
        match="event.detected_at is required",
    ):
        persist_risk_assessment_v2(
            db,
            event=event,
            semantic_magnitude=0.0,
            legacy_impact=0.0,
            semantic_severity=0.0,
            page_context=0.0,
            confidence=0.0,
        )