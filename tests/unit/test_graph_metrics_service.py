from __future__ import annotations

import uuid

from src.services.graph_metrics_service import (
    _bounded_ratio,
    _downstream_reach,
    calculate_graph_metrics,
)


def test_bounded_ratio_handles_zero_denominator():
    assert _bounded_ratio(
        1,
        0,
    ) == 0.0


def test_downstream_reach_handles_chain():
    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()
    fourth = uuid.uuid4()

    adjacency = {
        first: {second},
        second: {third},
        third: {fourth},
    }

    assert (
        _downstream_reach(
            source_entity_id=first,
            adjacency=adjacency,
        )
        == 3
    )

    assert (
        _downstream_reach(
            source_entity_id=second,
            adjacency=adjacency,
        )
        == 2
    )

    assert (
        _downstream_reach(
            source_entity_id=fourth,
            adjacency=adjacency,
        )
        == 0
    )


def test_downstream_reach_is_cycle_safe():
    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()

    adjacency = {
        first: {second},
        second: {third},
        third: {first},
    }

    assert (
        _downstream_reach(
            source_entity_id=first,
            adjacency=adjacency,
        )
        == 2
    )


def test_calculate_graph_metrics_for_directed_chain():
    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()
    fourth = uuid.uuid4()
    organization_id = uuid.uuid4()

    result = calculate_graph_metrics(
        organization_id=(
            organization_id
        ),
        entity_ids=[
            first,
            second,
            third,
            fourth,
        ],
        edges=[
            (first, second),
            (second, third),
            (third, fourth),
        ],
    )

    assert result.organization_id == (
        organization_id
    )
    assert result.entity_count == 4
    assert result.active_edge_count == 3

    by_entity = {
        item.entity_id: item
        for item in result.entities
    }

    first_metrics = by_entity[first]

    assert first_metrics.in_degree == 0
    assert first_metrics.out_degree == 1
    assert first_metrics.degree == 1
    assert (
        first_metrics.in_degree_centrality
        == 0.0
    )
    assert (
        first_metrics.out_degree_centrality
        == 0.333333
    )
    assert (
        first_metrics.degree_centrality
        == 0.166667
    )
    assert (
        first_metrics.downstream_reach
        == 3
    )
    assert (
        first_metrics.downstream_reach_ratio
        == 1.0
    )

    second_metrics = by_entity[
        second
    ]

    assert second_metrics.in_degree == 1
    assert second_metrics.out_degree == 1
    assert second_metrics.degree == 2
    assert (
        second_metrics.degree_centrality
        == 0.333333
    )
    assert (
        second_metrics.downstream_reach
        == 2
    )
    assert (
        second_metrics.downstream_reach_ratio
        == 0.666667
    )


def test_calculate_graph_metrics_deduplicates_edges():
    first = uuid.uuid4()
    second = uuid.uuid4()

    result = calculate_graph_metrics(
        organization_id=uuid.uuid4(),
        entity_ids=[
            first,
            second,
        ],
        edges=[
            (first, second),
            (first, second),
            (first, second),
        ],
    )

    assert result.active_edge_count == 1

    by_entity = {
        item.entity_id: item
        for item in result.entities
    }

    assert by_entity[first].out_degree == 1
    assert by_entity[second].in_degree == 1


def test_calculate_graph_metrics_ignores_invalid_edges():
    first = uuid.uuid4()
    second = uuid.uuid4()
    outside = uuid.uuid4()

    result = calculate_graph_metrics(
        organization_id=uuid.uuid4(),
        entity_ids=[
            first,
            second,
        ],
        edges=[
            (first, first),
            (first, outside),
            (outside, second),
        ],
    )

    assert result.active_edge_count == 0

    for metrics in result.entities:
        assert metrics.degree == 0
        assert metrics.downstream_reach == 0


def test_single_entity_graph_has_zero_centrality():
    entity_id = uuid.uuid4()

    result = calculate_graph_metrics(
        organization_id=uuid.uuid4(),
        entity_ids=[
            entity_id,
        ],
        edges=[],
    )

    metrics = result.entities[0]

    assert metrics.in_degree == 0
    assert metrics.out_degree == 0
    assert metrics.degree == 0
    assert metrics.in_degree_centrality == 0.0
    assert metrics.out_degree_centrality == 0.0
    assert metrics.degree_centrality == 0.0
    assert metrics.downstream_reach == 0
    assert metrics.downstream_reach_ratio == 0.0