from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.services.graph_metrics_service import (
    OrganizationGraphMetrics,
)
from src.services.risk_scoring_v2_orchestrator import (
    RiskScoringV2ValidationError,
)
from src.services.risk_v3_requalification_service import (
    _score_event,
    requalify_risk_v3,
)
from src.services.topology_shift_service import (
    EntityTopologyShift,
    OrganizationTopologyShift,
)
from src.services.risk_version_comparison_service import (
    RiskComparisonValue,
    compare_risk_values,
)


def _graph(
    organization_id: uuid.UUID,
    *,
    active_edge_count: int,
) -> OrganizationGraphMetrics:
    return OrganizationGraphMetrics(
        organization_id=organization_id,
        entity_count=0,
        active_edge_count=active_edge_count,
        entities=(),
    )


def _event(
    organization_id: uuid.UUID,
    detected_at: datetime,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        detected_at=detected_at,
    )


def _comparison(
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
):
    value = RiskComparisonValue(
        score=0.0,
        semantic_magnitude_contribution=0.0,
        legacy_impact_contribution=0.0,
        semantic_severity_contribution=0.0,
        page_context_contribution=0.0,
        confidence_contribution=0.0,
        topology_contribution=0.0,
        topology_score=0.0,
        topology_shift_component=0.0,
        topology_centrality_component=0.0,
        topology_downstream_component=0.0,
        has_topology_signal=True,
        has_previous_topology_state=False,
    )

    return compare_risk_values(
        organization_id=organization_id,
        event_id=event_id,
        baseline=value,
        candidate=value,
    )


class _NoAutoflush:
    def __init__(self):
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        self.exited = True
        return False


def test_same_timestamp_events_share_one_topology_transition():
    organization_id = uuid.uuid4()
    observed_at = datetime(
        2026,
        1,
        1,
        tzinfo=timezone.utc,
    )

    events = (
        _event(
            organization_id,
            observed_at,
        ),
        _event(
            organization_id,
            observed_at,
        ),
    )

    db = Mock()
    no_autoflush = _NoAutoflush()
    db.no_autoflush = no_autoflush

    current = _graph(
        organization_id,
        active_edge_count=1,
    )

    shift = object()
    score_calls = []

    def score_event(**kwargs):
        score_calls.append(kwargs)
        event = kwargs["event"]
        return _comparison(
            organization_id,
            event.id,
        )

    with (
        patch(
            "src.services.risk_v3_requalification_service."
            "_eligible_events",
            return_value=events,
        ),
        patch(
            "src.services.risk_v3_requalification_service."
            "calculate_organization_graph_metrics_as_of",
            return_value=current,
        ) as calculate_metrics,
        patch(
            "src.services.risk_v3_requalification_service."
            "compare_graph_metrics",
            return_value=shift,
        ) as compare_metrics,
        patch(
            "src.services.risk_v3_requalification_service."
            "_score_event",
            side_effect=score_event,
        ),
        patch(
            "src.services.risk_v3_requalification_service."
            "evaluate_risk_shadow_comparisons",
            return_value=object(),
        ),
        patch(
            "src.services.risk_v3_requalification_service."
            "summarize_risk_v3_qualification_evaluation",
            return_value=SimpleNamespace(
                previous_state_pairs=0,
                exercised_topology_pairs=0,
                divergent_pairs=0,
                topology_exercise_observed=False,
            ),
        ),
    ):
        result = requalify_risk_v3(
            db,
            organization_id=organization_id,
        )

    assert result.eligible_event_count == 2
    assert result.paired_event_count == 2
    assert result.invalid_event_count == 0
    assert result.distinct_timestamp_count == 1
    assert (
        result.qualification_evidence.previous_state_pairs
        == 0
    )
    assert (
        result.qualification_evidence.topology_exercise_observed
        is False
    )

    calculate_metrics.assert_called_once_with(
        db=db,
        organization_id=organization_id,
        observed_at=observed_at,
    )

    assert compare_metrics.call_count == 1

    assert len(score_calls) == 2
    assert all(
        call["shift"] is shift
        for call in score_calls
    )
    assert all(
        call["has_previous_state"] is False
        for call in score_calls
    )

    assert no_autoflush.entered is True
    assert no_autoflush.exited is True


def test_previous_state_advances_only_between_distinct_timestamps():
    organization_id = uuid.uuid4()
    first_at = datetime(
        2026,
        1,
        1,
        tzinfo=timezone.utc,
    )
    second_at = first_at + timedelta(
        hours=1
    )

    first_events = (
        _event(
            organization_id,
            first_at,
        ),
        _event(
            organization_id,
            first_at,
        ),
    )
    second_event = _event(
        organization_id,
        second_at,
    )

    events = (
        *first_events,
        second_event,
    )

    db = Mock()
    db.no_autoflush = _NoAutoflush()

    first_graph = _graph(
        organization_id,
        active_edge_count=1,
    )
    second_graph = _graph(
        organization_id,
        active_edge_count=2,
    )

    first_shift = object()
    second_shift = object()

    score_calls = []

    def score_event(**kwargs):
        score_calls.append(kwargs)
        event = kwargs["event"]
        return _comparison(
            organization_id,
            event.id,
        )

    with (
        patch(
            "src.services.risk_v3_requalification_service."
            "_eligible_events",
            return_value=events,
        ),
        patch(
            "src.services.risk_v3_requalification_service."
            "calculate_organization_graph_metrics_as_of",
            side_effect=(
                first_graph,
                second_graph,
            ),
        ) as calculate_metrics,
        patch(
            "src.services.risk_v3_requalification_service."
            "compare_graph_metrics",
            side_effect=(
                first_shift,
                second_shift,
            ),
        ) as compare_metrics,
        patch(
            "src.services.risk_v3_requalification_service."
            "_score_event",
            side_effect=score_event,
        ),
        patch(
            "src.services.risk_v3_requalification_service."
            "evaluate_risk_shadow_comparisons",
            return_value=object(),
        ),
        patch(
            "src.services.risk_v3_requalification_service."
            "summarize_risk_v3_qualification_evaluation",
            return_value=SimpleNamespace(
                previous_state_pairs=1,
                exercised_topology_pairs=1,
                divergent_pairs=0,
                topology_exercise_observed=True,
            ),
        ),
    ):
        result = requalify_risk_v3(
            db,
            organization_id=organization_id,
        )

    assert result.eligible_event_count == 3
    assert result.paired_event_count == 3
    assert result.invalid_event_count == 0
    assert result.distinct_timestamp_count == 2

    assert calculate_metrics.call_count == 2

    first_previous = (
        compare_metrics.call_args_list[
            0
        ].kwargs["previous"]
    )
    first_current = (
        compare_metrics.call_args_list[
            0
        ].kwargs["current"]
    )
    second_previous = (
        compare_metrics.call_args_list[
            1
        ].kwargs["previous"]
    )
    second_current = (
        compare_metrics.call_args_list[
            1
        ].kwargs["current"]
    )

    assert first_previous.active_edge_count == 0
    assert first_current is first_graph

    assert second_previous is first_graph
    assert second_current is second_graph

    assert [
        call["shift"]
        for call in score_calls
    ] == [
        first_shift,
        first_shift,
        second_shift,
    ]

    assert [
        call["has_previous_state"]
        for call in score_calls
    ] == [
        False,
        False,
        True,
    ]


def test_empty_eligible_set_is_read_only_and_returns_no_evaluation():
    organization_id = uuid.uuid4()

    db = Mock()
    no_autoflush = _NoAutoflush()
    db.no_autoflush = no_autoflush

    with (
        patch(
            "src.services.risk_v3_requalification_service."
            "_eligible_events",
            return_value=(),
        ),
        patch(
            "src.services.risk_v3_requalification_service."
            "calculate_organization_graph_metrics_as_of",
        ) as calculate_metrics,
        patch(
            "src.services.risk_v3_requalification_service."
            "compare_graph_metrics",
        ) as compare_metrics,
        patch(
            "src.services.risk_v3_requalification_service."
            "evaluate_risk_shadow_comparisons",
        ) as evaluate,
    ):
        result = requalify_risk_v3(
            db,
            organization_id=organization_id,
        )

    assert result.organization_id == organization_id
    assert result.eligible_event_count == 0
    assert result.paired_event_count == 0
    assert result.invalid_event_count == 0
    assert result.distinct_timestamp_count == 0
    assert result.comparisons == ()
    assert result.evaluation is None
    assert (
        result.qualification_evidence.previous_state_pairs
        == 0
    )
    assert (
        result.qualification_evidence.exercised_topology_pairs
        == 0
    )
    assert (
        result.qualification_evidence.divergent_pairs
        == 0
    )
    assert (
        result.qualification_evidence.topology_exercise_observed
        is False
    )

    calculate_metrics.assert_not_called()
    compare_metrics.assert_not_called()
    evaluate.assert_not_called()

    db.add.assert_not_called()
    db.flush.assert_not_called()
    db.commit.assert_not_called()
    db.delete.assert_not_called()

    assert no_autoflush.entered is True
    assert no_autoflush.exited is True

def test_score_event_uses_real_v2_and_v3_topology_semantics():
    organization_id = uuid.uuid4()
    event_id = uuid.uuid4()

    event = SimpleNamespace(
        id=event_id,
        organization_id=organization_id,
    )

    shift = OrganizationTopologyShift(
        organization_id=organization_id,
        previous_entity_count=2,
        current_entity_count=2,
        entity_count_delta=0,
        previous_active_edge_count=1,
        current_active_edge_count=2,
        active_edge_count_delta=1,
        changed_entity_count=2,
        max_shift_score=0.8,
        entities=(
            EntityTopologyShift(
                entity_id=uuid.UUID(
                    "00000000-0000-0000-0000-000000000001"
                ),
                previous_in_degree=0,
                current_in_degree=1,
                in_degree_delta=1,
                previous_out_degree=1,
                current_out_degree=1,
                out_degree_delta=0,
                previous_degree=1,
                current_degree=2,
                degree_delta=1,
                previous_degree_centrality=0.1,
                current_degree_centrality=0.3,
                degree_centrality_delta=0.2,
                previous_downstream_reach=1,
                current_downstream_reach=2,
                downstream_reach_delta=1,
                previous_downstream_reach_ratio=0.2,
                current_downstream_reach_ratio=0.3,
                downstream_reach_ratio_delta=0.1,
                classification="downstream_gain",
                severity="high",
                shift_score=0.8,
            ),
            EntityTopologyShift(
                entity_id=uuid.UUID(
                    "00000000-0000-0000-0000-000000000002"
                ),
                previous_in_degree=1,
                current_in_degree=1,
                in_degree_delta=0,
                previous_out_degree=0,
                current_out_degree=1,
                out_degree_delta=1,
                previous_degree=1,
                current_degree=2,
                degree_delta=1,
                previous_degree_centrality=0.1,
                current_degree_centrality=0.7,
                degree_centrality_delta=0.6,
                previous_downstream_reach=0,
                current_downstream_reach=1,
                downstream_reach_delta=1,
                previous_downstream_reach_ratio=0.0,
                current_downstream_reach_ratio=0.7,
                downstream_reach_ratio_delta=0.7,
                classification="downstream_gain",
                severity="high",
                shift_score=0.4,
            ),
        ),
    )

    normalized = SimpleNamespace(
        semantic_magnitude=0.5,
        legacy_impact=0.4,
        semantic_severity=0.3,
        page_context=0.2,
        confidence=0.6,
    )

    with patch(
        "src.services.risk_v3_requalification_service."
        "build_risk_v2_inputs",
        return_value=normalized,
    ):
        comparison = _score_event(
            event=event,
            shift=shift,
            has_previous_state=True,
        )

    assert comparison.organization_id == organization_id
    assert comparison.event_id == event_id

    assert (
        comparison.baseline_assessment_id
        is None
    )
    assert (
        comparison.candidate_assessment_id
        is None
    )

    assert (
        comparison.topology.baseline_shift_component
        == 0.8
    )
    assert (
        comparison.topology.candidate_shift_component
        == 0.8
    )

    assert (
        comparison.topology.baseline_centrality_component
        == 0.6
    )
    assert (
        comparison.topology.baseline_downstream_component
        == 0.7
    )

    assert (
        comparison.topology.candidate_centrality_component
        == 0.2
    )
    assert (
        comparison.topology.candidate_downstream_component
        == 0.1
    )

    assert comparison.score_delta != 0.0


def test_invalid_candidate_is_counted_without_aborting_requalification():
    organization_id = uuid.uuid4()
    observed_at = datetime(
        2026,
        1,
        1,
        tzinfo=timezone.utc,
    )

    invalid_event = _event(
        organization_id,
        observed_at,
    )
    valid_event = _event(
        organization_id,
        observed_at,
    )

    events = (
        invalid_event,
        valid_event,
    )

    db = Mock()
    db.no_autoflush = _NoAutoflush()

    current = _graph(
        organization_id,
        active_edge_count=1,
    )
    shift = object()

    valid_comparison = _comparison(
        organization_id,
        valid_event.id,
    )
    evaluation = object()

    with (
        patch(
            "src.services.risk_v3_requalification_service."
            "_eligible_events",
            return_value=events,
        ),
        patch(
            "src.services.risk_v3_requalification_service."
            "calculate_organization_graph_metrics_as_of",
            return_value=current,
        ),
        patch(
            "src.services.risk_v3_requalification_service."
            "compare_graph_metrics",
            return_value=shift,
        ),
        patch(
            "src.services.risk_v3_requalification_service."
            "_score_event",
            side_effect=(
                RiskScoringV2ValidationError(
                    "invalid persisted event evidence"
                ),
                valid_comparison,
            ),
        ),
        patch(
            "src.services.risk_v3_requalification_service."
            "evaluate_risk_shadow_comparisons",
            return_value=evaluation,
        ) as evaluate,
        patch(
            "src.services.risk_v3_requalification_service."
            "summarize_risk_v3_qualification_evaluation",
            return_value=object(),
        ),
    ):
        result = requalify_risk_v3(
            db,
            organization_id=organization_id,
        )

    assert result.eligible_event_count == 2
    assert result.paired_event_count == 1
    assert result.invalid_event_count == 1
    assert result.distinct_timestamp_count == 1
    assert result.comparisons == (
        valid_comparison,
    )
    assert result.evaluation is evaluation

    evaluate.assert_called_once_with(
        [
            valid_comparison,
        ]
    )
