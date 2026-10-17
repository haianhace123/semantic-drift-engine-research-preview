from __future__ import annotations

import uuid

from benchmarks.temporal_knowledge_integrity.topology_adapter import (
    ProductionTopologyAdapter,
    graph_state,
)


def test_identical_graph_scores_zero() -> None:
    organization_id = uuid.uuid4()
    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()

    state = graph_state(
        entity_ids=(
            first,
            second,
            third,
        ),
        edges=(
            (first, second),
            (second, third),
        ),
    )

    result = ProductionTopologyAdapter().score(
        organization_id=organization_id,
        previous=state,
        current=state,
    )

    assert result.score == 0.0
    assert result.shift.changed_entity_count == 0
    assert result.shift.active_edge_count_delta == 0


def test_new_downstream_branch_scores_structural_change() -> None:
    organization_id = uuid.uuid4()
    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()
    fourth = uuid.uuid4()

    previous = graph_state(
        entity_ids=(
            first,
            second,
            third,
            fourth,
        ),
        edges=(
            (first, second),
            (second, third),
        ),
    )
    current = graph_state(
        entity_ids=(
            first,
            second,
            third,
            fourth,
        ),
        edges=(
            (first, second),
            (second, third),
            (second, fourth),
        ),
    )

    result = ProductionTopologyAdapter().score(
        organization_id=organization_id,
        previous=previous,
        current=current,
    )

    by_entity = {
        shift.entity_id: shift
        for shift in result.shift.entities
    }

    assert result.score > 0.0
    assert result.shift.active_edge_count_delta == 1
    assert by_entity[first].downstream_reach_delta == 1
    assert by_entity[second].downstream_reach_delta == 1


def test_rewire_can_score_change_without_edge_count_delta() -> None:
    organization_id = uuid.uuid4()
    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()
    fourth = uuid.uuid4()

    previous = graph_state(
        entity_ids=(
            first,
            second,
            third,
            fourth,
        ),
        edges=(
            (first, second),
            (second, third),
        ),
    )
    current = graph_state(
        entity_ids=(
            first,
            second,
            third,
            fourth,
        ),
        edges=(
            (first, second),
            (third, fourth),
        ),
    )

    result = ProductionTopologyAdapter().score(
        organization_id=organization_id,
        previous=previous,
        current=current,
    )

    assert result.shift.active_edge_count_delta == 0
    assert result.shift.changed_entity_count > 0
    assert result.score > 0.0


def test_entity_population_change_uses_production_union_semantics() -> None:
    organization_id = uuid.uuid4()
    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()

    previous = graph_state(
        entity_ids=(
            first,
            second,
        ),
        edges=(
            (first, second),
        ),
    )
    current = graph_state(
        entity_ids=(
            first,
            second,
            third,
        ),
        edges=(
            (first, second),
            (second, third),
        ),
    )

    result = ProductionTopologyAdapter().score(
        organization_id=organization_id,
        previous=previous,
        current=current,
    )

    by_entity = {
        shift.entity_id: shift
        for shift in result.shift.entities
    }

    assert result.shift.previous_entity_count == 2
    assert result.shift.current_entity_count == 3
    assert result.shift.entity_count_delta == 1
    assert third in by_entity
    assert by_entity[third].previous_degree == 0
    assert by_entity[third].current_degree == 1


def test_graph_state_materializes_iterables_once() -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()

    entity_ids = (
        entity_id
        for entity_id in (
            first,
            second,
        )
    )
    edges = (
        edge
        for edge in (
            (first, second),
        )
    )

    state = graph_state(
        entity_ids=entity_ids,
        edges=edges,
    )

    assert state.entity_ids == (
        first,
        second,
    )
    assert state.edges == (
        (first, second),
    )