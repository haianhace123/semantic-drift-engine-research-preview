from __future__ import annotations

import uuid

import pytest

from src.services.graph_metrics_service import (
    calculate_graph_metrics,
)
from src.services.topology_shift_service import (
    _severity_from_score,
    compare_graph_metrics,
)


def test_identical_graph_is_stable():
    organization_id = uuid.uuid4()

    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()

    previous = calculate_graph_metrics(
        organization_id=organization_id,
        entity_ids=[
            first,
            second,
            third,
        ],
        edges=[
            (first, second),
            (second, third),
        ],
    )

    current = calculate_graph_metrics(
        organization_id=organization_id,
        entity_ids=[
            first,
            second,
            third,
        ],
        edges=[
            (first, second),
            (second, third),
        ],
    )

    result = compare_graph_metrics(
        previous=previous,
        current=current,
    )

    assert result.organization_id == (
        organization_id
    )

    assert result.entity_count_delta == 0
    assert result.active_edge_count_delta == 0
    assert result.changed_entity_count == 0
    assert result.max_shift_score == 0.0

    assert all(
        shift.classification == "stable"
        for shift in result.entities
    )

    assert all(
        shift.severity == "none"
        for shift in result.entities
    )


def test_new_downstream_branch_detects_downstream_gain():
    organization_id = uuid.uuid4()

    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()
    fourth = uuid.uuid4()

    previous = calculate_graph_metrics(
        organization_id=organization_id,
        entity_ids=[
            first,
            second,
            third,
            fourth,
        ],
        edges=[
            (first, second),
            (second, third),
        ],
    )

    current = calculate_graph_metrics(
        organization_id=organization_id,
        entity_ids=[
            first,
            second,
            third,
            fourth,
        ],
        edges=[
            (first, second),
            (second, third),
            (second, fourth),
        ],
    )

    result = compare_graph_metrics(
        previous=previous,
        current=current,
    )

    by_entity = {
        shift.entity_id: shift
        for shift in result.entities
    }

    first_shift = by_entity[first]
    second_shift = by_entity[second]
    fourth_shift = by_entity[fourth]

    assert result.active_edge_count_delta == 1

    # A can now reach one additional downstream entity,
    # even though A itself gained no direct edge.
    assert first_shift.degree_delta == 0
    assert first_shift.downstream_reach_delta == 1
    assert (
        first_shift.classification
        == "downstream_gain"
    )
    assert first_shift.shift_score > 0.0

    # B gained a direct dependency and additional reach.
    assert second_shift.out_degree_delta == 1
    assert second_shift.degree_delta == 1
    assert second_shift.downstream_reach_delta == 1
    assert (
        second_shift.classification
        == "downstream_gain"
    )

    # D gained an incoming relationship but no downstream reach.
    assert fourth_shift.in_degree_delta == 1
    assert fourth_shift.downstream_reach_delta == 0
    assert (
        fourth_shift.classification
        == "centrality_gain"
    )


def test_removed_branch_detects_downstream_loss():
    organization_id = uuid.uuid4()

    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()
    fourth = uuid.uuid4()

    previous = calculate_graph_metrics(
        organization_id=organization_id,
        entity_ids=[
            first,
            second,
            third,
            fourth,
        ],
        edges=[
            (first, second),
            (second, third),
            (second, fourth),
        ],
    )

    current = calculate_graph_metrics(
        organization_id=organization_id,
        entity_ids=[
            first,
            second,
            third,
            fourth,
        ],
        edges=[
            (first, second),
            (second, third),
        ],
    )

    result = compare_graph_metrics(
        previous=previous,
        current=current,
    )

    by_entity = {
        shift.entity_id: shift
        for shift in result.entities
    }

    assert result.active_edge_count_delta == -1

    assert (
        by_entity[first].downstream_reach_delta
        == -1
    )
    assert (
        by_entity[first].classification
        == "downstream_loss"
    )

    assert (
        by_entity[second].downstream_reach_delta
        == -1
    )
    assert (
        by_entity[second].classification
        == "downstream_loss"
    )

    assert by_entity[fourth].in_degree_delta == -1
    assert (
        by_entity[fourth].classification
        == "centrality_loss"
    )


def test_rewired_edge_can_change_topology_without_edge_count_change():
    organization_id = uuid.uuid4()

    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()
    fourth = uuid.uuid4()

    previous = calculate_graph_metrics(
        organization_id=organization_id,
        entity_ids=[
            first,
            second,
            third,
            fourth,
        ],
        edges=[
            (first, second),
            (second, third),
        ],
    )

    current = calculate_graph_metrics(
        organization_id=organization_id,
        entity_ids=[
            first,
            second,
            third,
            fourth,
        ],
        edges=[
            (first, second),
            (third, fourth),
        ],
    )

    result = compare_graph_metrics(
        previous=previous,
        current=current,
    )

    assert result.active_edge_count_delta == 0
    assert result.changed_entity_count > 0
    assert result.max_shift_score > 0.0


def test_entity_population_change_uses_union():
    organization_id = uuid.uuid4()

    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()

    previous = calculate_graph_metrics(
        organization_id=organization_id,
        entity_ids=[
            first,
            second,
        ],
        edges=[
            (first, second),
        ],
    )

    current = calculate_graph_metrics(
        organization_id=organization_id,
        entity_ids=[
            first,
            second,
            third,
        ],
        edges=[
            (first, second),
            (second, third),
        ],
    )

    result = compare_graph_metrics(
        previous=previous,
        current=current,
    )

    assert result.previous_entity_count == 2
    assert result.current_entity_count == 3
    assert result.entity_count_delta == 1

    by_entity = {
        shift.entity_id: shift
        for shift in result.entities
    }

    assert third in by_entity
    assert by_entity[third].previous_degree == 0
    assert by_entity[third].current_degree == 1
    assert by_entity[third].in_degree_delta == 1


def test_cross_organization_comparison_is_rejected():
    first = uuid.uuid4()
    second = uuid.uuid4()

    previous = calculate_graph_metrics(
        organization_id=uuid.uuid4(),
        entity_ids=[
            first,
            second,
        ],
        edges=[
            (first, second),
        ],
    )

    current = calculate_graph_metrics(
        organization_id=uuid.uuid4(),
        entity_ids=[
            first,
            second,
        ],
        edges=[
            (first, second),
        ],
    )

    with pytest.raises(
        ValueError,
        match="different organizations",
    ):
        compare_graph_metrics(
            previous=previous,
            current=current,
        )


def test_result_entity_order_is_deterministic():
    organization_id = uuid.uuid4()

    entity_ids = [
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    ]

    previous = calculate_graph_metrics(
        organization_id=organization_id,
        entity_ids=reversed(entity_ids),
        edges=[],
    )

    current = calculate_graph_metrics(
        organization_id=organization_id,
        entity_ids=entity_ids,
        edges=[
            (
                entity_ids[0],
                entity_ids[1],
            ),
        ],
    )

    result = compare_graph_metrics(
        previous=previous,
        current=current,
    )

    assert [
        shift.entity_id
        for shift in result.entities
    ] == sorted(
        entity_ids,
        key=str,
    )


def test_severity_thresholds_are_deterministic():
    assert _severity_from_score(0.0) == "none"

    assert (
        _severity_from_score(0.000001)
        == "low"
    )

    assert (
        _severity_from_score(0.199999)
        == "low"
    )

    assert (
        _severity_from_score(0.20)
        == "medium"
    )

    assert (
        _severity_from_score(0.499999)
        == "medium"
    )

    assert (
        _severity_from_score(0.50)
        == "high"
    )

    assert _severity_from_score(1.0) == "high"