from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.services.event_topology_risk_service import (
    load_event_topology_risk_signal,
)


def _event():
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )


def _row(
    *,
    organization_id,
    entity_id=None,
    observed_at=None,
    previous_observed_at=None,
    shift_score=0.0,
    degree_centrality_delta=0.0,
    downstream_reach_ratio_delta=0.0,
):
    return SimpleNamespace(
        organization_id=organization_id,
        entity_id=entity_id or uuid.uuid4(),
        observed_at=(
            observed_at
            or datetime(
                2026,
                8,
                30,
                10,
                0,
                tzinfo=timezone.utc,
            )
        ),
        previous_observed_at=(
            previous_observed_at
        ),
        shift_score=shift_score,
        degree_centrality_delta=(
            degree_centrality_delta
        ),
        downstream_reach_ratio_delta=(
            downstream_reach_ratio_delta
        ),
    )


def _db_returning(rows):
    db = MagicMock()

    query = db.query.return_value
    filtered = query.filter.return_value
    ordered = filtered.order_by.return_value
    ordered.all.return_value = rows

    return db


def test_returns_none_when_event_has_no_topology_snapshots():
    event = _event()
    db = _db_returning([])

    result = load_event_topology_risk_signal(
        db,
        event,
    )

    assert result is None


def test_aggregates_independent_maximum_structural_signals():
    event = _event()

    previous_at = datetime(
        2026,
        8,
        30,
        9,
        0,
        tzinfo=timezone.utc,
    )

    observed_at = datetime(
        2026,
        8,
        30,
        10,
        0,
        tzinfo=timezone.utc,
    )

    rows = [
        _row(
            organization_id=event.organization_id,
            observed_at=observed_at,
            previous_observed_at=previous_at,
            shift_score=0.8,
            degree_centrality_delta=0.1,
            downstream_reach_ratio_delta=0.2,
        ),
        _row(
            organization_id=event.organization_id,
            observed_at=observed_at,
            previous_observed_at=previous_at,
            shift_score=0.3,
            degree_centrality_delta=-0.7,
            downstream_reach_ratio_delta=0.1,
        ),
        _row(
            organization_id=event.organization_id,
            observed_at=observed_at,
            previous_observed_at=previous_at,
            shift_score=0.4,
            degree_centrality_delta=0.2,
            downstream_reach_ratio_delta=-0.9,
        ),
    ]

    result = load_event_topology_risk_signal(
        _db_returning(rows),
        event,
    )

    assert result is not None

    assert result.entity_count == 3
    assert result.shift_score == 0.8

    assert (
        result.degree_centrality_delta
        == 0.7
    )

    assert (
        result.downstream_reach_ratio_delta
        == 0.9
    )

    assert result.has_previous_state is True

    assert result.risk_input.shift_score == 0.8

    assert (
        result.risk_input.degree_centrality_delta
        == 0.7
    )

    assert (
        result.risk_input.downstream_reach_ratio_delta
        == 0.9
    )

    assert (
        result.risk_input.has_previous_state
        is True
    )


def test_baseline_state_is_preserved_as_non_risk_topology():
    event = _event()

    observed_at = datetime(
        2026,
        8,
        30,
        10,
        0,
        tzinfo=timezone.utc,
    )

    rows = [
        _row(
            organization_id=event.organization_id,
            observed_at=observed_at,
            previous_observed_at=None,
            shift_score=1.0,
            degree_centrality_delta=1.0,
            downstream_reach_ratio_delta=1.0,
        )
    ]

    result = load_event_topology_risk_signal(
        _db_returning(rows),
        event,
    )

    assert result is not None
    assert result.has_previous_state is False

    assert (
        result.risk_input.has_previous_state
        is False
    )


def test_absolute_direction_is_used_for_structural_movement():
    event = _event()

    previous_at = datetime(
        2026,
        8,
        30,
        9,
        0,
        tzinfo=timezone.utc,
    )

    rows = [
        _row(
            organization_id=event.organization_id,
            previous_observed_at=previous_at,
            degree_centrality_delta=-0.6,
            downstream_reach_ratio_delta=-0.75,
        )
    ]

    result = load_event_topology_risk_signal(
        _db_returning(rows),
        event,
    )

    assert result is not None

    assert (
        result.degree_centrality_delta
        == 0.6
    )

    assert (
        result.downstream_reach_ratio_delta
        == 0.75
    )


def test_rejects_inconsistent_previous_state():
    event = _event()

    observed_at = datetime(
        2026,
        8,
        30,
        10,
        0,
        tzinfo=timezone.utc,
    )

    previous_at = datetime(
        2026,
        8,
        30,
        9,
        0,
        tzinfo=timezone.utc,
    )

    rows = [
        _row(
            organization_id=event.organization_id,
            observed_at=observed_at,
            previous_observed_at=previous_at,
        ),
        _row(
            organization_id=event.organization_id,
            observed_at=observed_at,
            previous_observed_at=None,
        ),
    ]

    with pytest.raises(
        RuntimeError,
        match="inconsistent previous_observed_at",
    ):
        load_event_topology_risk_signal(
            _db_returning(rows),
            event,
        )


def test_rejects_inconsistent_observation_time():
    event = _event()

    previous_at = datetime(
        2026,
        8,
        30,
        9,
        0,
        tzinfo=timezone.utc,
    )

    rows = [
        _row(
            organization_id=event.organization_id,
            observed_at=datetime(
                2026,
                8,
                30,
                10,
                0,
                tzinfo=timezone.utc,
            ),
            previous_observed_at=previous_at,
        ),
        _row(
            organization_id=event.organization_id,
            observed_at=datetime(
                2026,
                8,
                30,
                11,
                0,
                tzinfo=timezone.utc,
            ),
            previous_observed_at=previous_at,
        ),
    ]

    with pytest.raises(
        RuntimeError,
        match="inconsistent observed_at",
    ):
        load_event_topology_risk_signal(
            _db_returning(rows),
            event,
        )


def test_rejects_tenant_mismatch():
    event = _event()

    rows = [
        _row(
            organization_id=uuid.uuid4(),
        )
    ]

    with pytest.raises(
        RuntimeError,
        match="tenant mismatch",
    ):
        load_event_topology_risk_signal(
            _db_returning(rows),
            event,
        )


def test_requires_event_id():
    event = SimpleNamespace(
        id=None,
        organization_id=uuid.uuid4(),
    )

    with pytest.raises(
        ValueError,
        match="event.id is required",
    ):
        load_event_topology_risk_signal(
            MagicMock(),
            event,
        )


def test_requires_event_organization_id():
    event = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=None,
    )

    with pytest.raises(
        ValueError,
        match="event.organization_id is required",
    ):
        load_event_topology_risk_signal(
            MagicMock(),
            event,
        )