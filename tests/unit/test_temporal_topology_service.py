from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.services.temporal_topology_service import (
    _snapshot_rows_to_graph_metrics,
)


def _row(
    *,
    organization_id,
    entity_id,
    entity_count=2,
    active_edge_count=1,
    in_degree=0,
    out_degree=0,
    degree=0,
    in_degree_centrality=0.0,
    out_degree_centrality=0.0,
    degree_centrality=0.0,
    downstream_reach=0,
    downstream_reach_ratio=0.0,
):
    return SimpleNamespace(
        organization_id=organization_id,
        entity_id=entity_id,
        entity_count=entity_count,
        active_edge_count=active_edge_count,
        in_degree=in_degree,
        out_degree=out_degree,
        degree=degree,
        in_degree_centrality=(
            in_degree_centrality
        ),
        out_degree_centrality=(
            out_degree_centrality
        ),
        degree_centrality=(
            degree_centrality
        ),
        downstream_reach=(
            downstream_reach
        ),
        downstream_reach_ratio=(
            downstream_reach_ratio
        ),
    )


def test_reconstructs_graph_metrics_from_snapshot_rows():
    organization_id = uuid.uuid4()

    first = uuid.uuid4()
    second = uuid.uuid4()

    rows = [
        _row(
            organization_id=organization_id,
            entity_id=second,
            in_degree=1,
            degree=1,
            in_degree_centrality=1.0,
            degree_centrality=0.5,
        ),
        _row(
            organization_id=organization_id,
            entity_id=first,
            out_degree=1,
            degree=1,
            out_degree_centrality=1.0,
            degree_centrality=0.5,
            downstream_reach=1,
            downstream_reach_ratio=1.0,
        ),
    ]

    result = _snapshot_rows_to_graph_metrics(
        organization_id=organization_id,
        rows=rows,
    )

    assert result.organization_id == (
        organization_id
    )
    assert result.entity_count == 2
    assert result.active_edge_count == 1

    assert [
        item.entity_id
        for item in result.entities
    ] == sorted(
        [first, second],
        key=str,
    )

    by_entity = {
        item.entity_id: item
        for item in result.entities
    }

    assert by_entity[first].out_degree == 1
    assert (
        by_entity[first].downstream_reach
        == 1
    )

    assert by_entity[second].in_degree == 1


def test_empty_rows_return_empty_graph():
    organization_id = uuid.uuid4()

    result = _snapshot_rows_to_graph_metrics(
        organization_id=organization_id,
        rows=[],
    )

    assert result.organization_id == (
        organization_id
    )
    assert result.entity_count == 0
    assert result.active_edge_count == 0
    assert result.entities == ()


def test_rejects_cross_tenant_snapshot_row():
    organization_id = uuid.uuid4()

    rows = [
        _row(
            organization_id=uuid.uuid4(),
            entity_id=uuid.uuid4(),
            entity_count=1,
            active_edge_count=0,
        ),
    ]

    with pytest.raises(
        RuntimeError,
        match="organization",
    ):
        _snapshot_rows_to_graph_metrics(
            organization_id=organization_id,
            rows=rows,
        )


def test_rejects_inconsistent_entity_count():
    organization_id = uuid.uuid4()

    rows = [
        _row(
            organization_id=organization_id,
            entity_id=uuid.uuid4(),
            entity_count=2,
        ),
        _row(
            organization_id=organization_id,
            entity_id=uuid.uuid4(),
            entity_count=3,
        ),
    ]

    with pytest.raises(
        RuntimeError,
        match="entity_count",
    ):
        _snapshot_rows_to_graph_metrics(
            organization_id=organization_id,
            rows=rows,
        )


def test_rejects_inconsistent_active_edge_count():
    organization_id = uuid.uuid4()

    rows = [
        _row(
            organization_id=organization_id,
            entity_id=uuid.uuid4(),
            active_edge_count=1,
        ),
        _row(
            organization_id=organization_id,
            entity_id=uuid.uuid4(),
            active_edge_count=2,
        ),
    ]

    with pytest.raises(
        RuntimeError,
        match="active_edge_count",
    ):
        _snapshot_rows_to_graph_metrics(
            organization_id=organization_id,
            rows=rows,
        )


def test_rejects_duplicate_entity_rows():
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    rows = [
        _row(
            organization_id=organization_id,
            entity_id=entity_id,
        ),
        _row(
            organization_id=organization_id,
            entity_id=entity_id,
        ),
    ]

    with pytest.raises(
        RuntimeError,
        match="duplicate entity",
    ):
        _snapshot_rows_to_graph_metrics(
            organization_id=organization_id,
            rows=rows,
        )


def test_rejects_incomplete_snapshot_set():
    organization_id = uuid.uuid4()

    rows = [
        _row(
            organization_id=organization_id,
            entity_id=uuid.uuid4(),
            entity_count=2,
        ),
    ]

    with pytest.raises(
        RuntimeError,
        match="incomplete",
    ):
        _snapshot_rows_to_graph_metrics(
            organization_id=organization_id,
            rows=rows,
        )