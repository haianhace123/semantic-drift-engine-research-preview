import uuid

import pytest

from src.services.event_topology_risk_v3_service import (
    EventTopologyRiskV3Evidence,
    reduce_event_topology_risk_signal_v3,
)


def _evidence(
    entity_id,
    *,
    shift,
    centrality,
    downstream,
):
    return EventTopologyRiskV3Evidence(
        entity_id=entity_id,
        shift_score=shift,
        degree_centrality_delta=centrality,
        downstream_reach_ratio_delta=downstream,
    )


def test_reducer_keeps_components_from_representative_entity():
    organization_id = uuid.uuid4()
    event_id = uuid.uuid4()

    strongest = uuid.uuid4()

    result = reduce_event_topology_risk_signal_v3(
        organization_id=organization_id,
        event_id=event_id,
        evidence=(
            _evidence(
                uuid.uuid4(),
                shift=0.4,
                centrality=0.9,
                downstream=-0.8,
            ),
            _evidence(
                strongest,
                shift=0.8,
                centrality=-0.2,
                downstream=0.3,
            ),
        ),
        has_previous_state=True,
    )

    assert result.representative_entity_id == strongest
    assert result.shift_score == 0.8
    assert result.degree_centrality_delta == -0.2
    assert result.downstream_reach_ratio_delta == 0.3
    assert result.has_previous_state is True


def test_reducer_breaks_equal_shift_ties_by_smallest_uuid():
    organization_id = uuid.uuid4()
    event_id = uuid.uuid4()

    smaller = uuid.UUID(int=1)
    larger = uuid.UUID(int=2)

    result = reduce_event_topology_risk_signal_v3(
        organization_id=organization_id,
        event_id=event_id,
        evidence=(
            _evidence(
                larger,
                shift=0.7,
                centrality=0.8,
                downstream=0.9,
            ),
            _evidence(
                smaller,
                shift=0.7,
                centrality=-0.2,
                downstream=-0.3,
            ),
        ),
        has_previous_state=True,
    )

    assert result.representative_entity_id == smaller
    assert result.degree_centrality_delta == -0.2
    assert result.downstream_reach_ratio_delta == -0.3


def test_reducer_rejects_empty_evidence():
    with pytest.raises(
        ValueError,
        match="must not be empty",
    ):
        reduce_event_topology_risk_signal_v3(
            organization_id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            evidence=(),
            has_previous_state=True,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("shift", 1.01),
        ("centrality", -1.01),
        ("downstream", 1.01),
    ),
)
def test_reducer_preserves_strict_v3_validation(
    field,
    value,
):
    values = {
        "shift": 0.5,
        "centrality": 0.2,
        "downstream": -0.3,
    }
    values[field] = value

    with pytest.raises(ValueError):
        reduce_event_topology_risk_signal_v3(
            organization_id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            evidence=(
                _evidence(
                    uuid.uuid4(),
                    **values,
                ),
            ),
            has_previous_state=True,
        )
