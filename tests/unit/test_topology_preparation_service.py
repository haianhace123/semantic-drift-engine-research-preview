from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services.topology_preparation_service import (
    TopologyPreparationError,
    TopologyPreparationNotReadyError,
    prepare_topology_for_event,
)


def _event(
    *,
    vector_status="completed",
    semantic_magnitude=0.5,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        detected_at=datetime.now(
            timezone.utc
        ),
        vector_analysis_status=vector_status,
        semantic_magnitude=semantic_magnitude,
    )


def test_prepares_graph_before_topology(
    monkeypatch,
):
    db = Mock()
    event = _event()
    calls = []

    graph_result = SimpleNamespace(
        created=3,
        existing=0,
    )

    topology_result = SimpleNamespace(
        created=3,
        existing=0,
    )

    def graph(*, db, event):
        calls.append("graph")
        return graph_result

    def topology(*, db, event):
        calls.append("topology")
        return topology_result

    monkeypatch.setattr(
        (
            "src.services."
            "topology_preparation_service."
            "persist_graph_metric_snapshots_for_event"
        ),
        graph,
    )

    monkeypatch.setattr(
        (
            "src.services."
            "topology_preparation_service."
            "persist_topology_shift_snapshots_for_event"
        ),
        topology,
    )

    result = prepare_topology_for_event(
        db,
        event=event,
    )

    assert calls == [
        "graph",
        "topology",
    ]

    assert result.organization_id == (
        event.organization_id
    )
    assert result.event_id == event.id

    assert result.graph_created == 3
    assert result.graph_existing == 0

    assert result.topology_created == 3
    assert result.topology_existing == 0


def test_retry_reuses_existing_evidence(
    monkeypatch,
):
    db = Mock()
    event = _event()

    monkeypatch.setattr(
        (
            "src.services."
            "topology_preparation_service."
            "persist_graph_metric_snapshots_for_event"
        ),
        lambda **kwargs: SimpleNamespace(
            created=0,
            existing=4,
        ),
    )

    monkeypatch.setattr(
        (
            "src.services."
            "topology_preparation_service."
            "persist_topology_shift_snapshots_for_event"
        ),
        lambda **kwargs: SimpleNamespace(
            created=0,
            existing=4,
        ),
    )

    result = prepare_topology_for_event(
        db,
        event=event,
    )

    assert result.graph_created == 0
    assert result.graph_existing == 4

    assert result.topology_created == 0
    assert result.topology_existing == 4


def test_requires_vector_completion():
    db = Mock()

    with pytest.raises(
        TopologyPreparationNotReadyError,
        match="completed vector analysis",
    ):
        prepare_topology_for_event(
            db,
            event=_event(
                vector_status="pending",
            ),
        )


def test_requires_semantic_magnitude():
    db = Mock()

    with pytest.raises(
        TopologyPreparationNotReadyError,
        match="semantic_magnitude",
    ):
        prepare_topology_for_event(
            db,
            event=_event(
                semantic_magnitude=None,
            ),
        )


def test_requires_event_id():
    db = Mock()
    event = _event()
    event.id = None

    with pytest.raises(
        TopologyPreparationError,
        match="event.id is required",
    ):
        prepare_topology_for_event(
            db,
            event=event,
        )


def test_requires_organization_id():
    db = Mock()
    event = _event()
    event.organization_id = None

    with pytest.raises(
        TopologyPreparationError,
        match="event.organization_id is required",
    ):
        prepare_topology_for_event(
            db,
            event=event,
        )


def test_requires_detected_at():
    db = Mock()
    event = _event()
    event.detected_at = None

    with pytest.raises(
        TopologyPreparationError,
        match="event.detected_at is required",
    ):
        prepare_topology_for_event(
            db,
            event=event,
        )