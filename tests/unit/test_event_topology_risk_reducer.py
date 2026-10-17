import uuid

import pytest

from src.services.event_topology_risk_service import (
    EventTopologyRiskEvidence,
    reduce_event_topology_risk_signal,
)


def _evidence(
    *,
    shift,
    centrality,
    downstream,
):
    return EventTopologyRiskEvidence(
        shift_score=shift,
        degree_centrality_delta=centrality,
        downstream_reach_ratio_delta=downstream,
    )


def test_reducer_preserves_independent_maximum_semantics():
    result = reduce_event_topology_risk_signal(
        organization_id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        evidence=(
            _evidence(
                shift=0.8,
                centrality=0.1,
                downstream=0.2,
            ),
            _evidence(
                shift=0.3,
                centrality=-0.7,
                downstream=0.1,
            ),
            _evidence(
                shift=0.4,
                centrality=0.2,
                downstream=-0.9,
            ),
        ),
        has_previous_state=True,
    )

    assert result.entity_count == 3
    assert result.shift_score == 0.8
    assert result.degree_centrality_delta == 0.7
    assert result.downstream_reach_ratio_delta == 0.9
    assert result.has_previous_state is True

    assert result.risk_input.shift_score == 0.8
    assert result.risk_input.degree_centrality_delta == 0.7
    assert result.risk_input.downstream_reach_ratio_delta == 0.9
    assert result.risk_input.has_previous_state is True


def test_reducer_preserves_baseline_state():
    result = reduce_event_topology_risk_signal(
        organization_id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        evidence=(
            _evidence(
                shift=1.0,
                centrality=1.0,
                downstream=1.0,
            ),
        ),
        has_previous_state=False,
    )

    assert result.has_previous_state is False
    assert result.risk_input.has_previous_state is False


def test_reducer_preserves_v2_clamping_semantics():
    result = reduce_event_topology_risk_signal(
        organization_id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        evidence=(
            _evidence(
                shift=1.5,
                centrality=-1.4,
                downstream=2.0,
            ),
            _evidence(
                shift=-0.5,
                centrality=0.2,
                downstream=-0.3,
            ),
        ),
        has_previous_state=True,
    )

    assert result.shift_score == 1.0
    assert result.degree_centrality_delta == 1.0
    assert result.downstream_reach_ratio_delta == 1.0


def test_reducer_rejects_empty_evidence():
    with pytest.raises(
        ValueError,
        match="must not be empty",
    ):
        reduce_event_topology_risk_signal(
            organization_id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            evidence=(),
            has_previous_state=True,
        )
