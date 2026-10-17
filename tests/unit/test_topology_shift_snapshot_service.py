from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services.topology_shift_service import (
    EntityTopologyShift,
    OrganizationTopologyShift,
)
from src.services.temporal_topology_service import (
    TemporalTopologyComparison,
)
from src.services.topology_shift_snapshot_service import (
    _validate_event,
    persist_topology_shift_snapshots_for_event,
)


def _event(
    *,
    event_id=None,
    organization_id=None,
    detected_at=None,
):
    return SimpleNamespace(
        id=event_id,
        organization_id=organization_id,
        detected_at=detected_at,
    )


def _entity_shift(
    entity_id: uuid.UUID,
) -> EntityTopologyShift:
    return EntityTopologyShift(
        entity_id=entity_id,
        previous_in_degree=0,
        current_in_degree=1,
        in_degree_delta=1,
        previous_out_degree=0,
        current_out_degree=0,
        out_degree_delta=0,
        previous_degree=0,
        current_degree=1,
        degree_delta=1,
        previous_degree_centrality=0.0,
        current_degree_centrality=0.25,
        degree_centrality_delta=0.25,
        previous_downstream_reach=0,
        current_downstream_reach=0,
        downstream_reach_delta=0,
        previous_downstream_reach_ratio=0.0,
        current_downstream_reach_ratio=0.0,
        downstream_reach_ratio_delta=0.0,
        classification="centrality_gain",
        severity="medium",
        shift_score=0.25,
    )


def _comparison(
    *,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
    observed_at: datetime,
    entity_id: uuid.UUID,
) -> TemporalTopologyComparison:
    shift = OrganizationTopologyShift(
        organization_id=organization_id,
        previous_entity_count=2,
        current_entity_count=3,
        entity_count_delta=1,
        previous_active_edge_count=1,
        current_active_edge_count=2,
        active_edge_count_delta=1,
        changed_entity_count=1,
        max_shift_score=0.25,
        entities=(
            _entity_shift(entity_id),
        ),
    )

    return TemporalTopologyComparison(
        organization_id=organization_id,
        current_event_id=event_id,
        current_observed_at=observed_at,
        previous_event_id=uuid.uuid4(),
        previous_observed_at=datetime(
            2026, 8, 30, 9, 0,
            tzinfo=timezone.utc,
        ),
        has_previous_state=True,
        shift=shift,
    )


def test_validate_event_requires_id():
    with pytest.raises(
        ValueError,
        match="must have an id",
    ):
        _validate_event(
            _event(
                organization_id=uuid.uuid4(),
                detected_at=datetime.now(
                    timezone.utc
                ),
            )
        )


def test_validate_event_requires_organization():
    with pytest.raises(
        ValueError,
        match="organization",
    ):
        _validate_event(
            _event(
                event_id=uuid.uuid4(),
                detected_at=datetime.now(
                    timezone.utc
                ),
            )
        )


def test_validate_event_requires_detected_at():
    with pytest.raises(
        ValueError,
        match="detected_at",
    ):
        _validate_event(
            _event(
                event_id=uuid.uuid4(),
                organization_id=uuid.uuid4(),
            )
        )


def test_persistence_uses_temporal_comparison():
    organization_id = uuid.uuid4()
    event_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    observed_at = datetime(
        2026, 8, 30, 10, 0,
        tzinfo=timezone.utc,
    )

    event = _event(
        event_id=event_id,
        organization_id=organization_id,
        detected_at=observed_at,
    )

    comparison = _comparison(
        organization_id=organization_id,
        event_id=event_id,
        observed_at=observed_at,
        entity_id=entity_id,
    )

    db = MagicMock()

    with (
        patch(
            "src.services."
            "topology_shift_snapshot_service."
            "compare_event_topology",
            return_value=comparison,
        ),
        patch(
            "src.services."
            "topology_shift_snapshot_service."
            "_persist_entity_shift",
            return_value=(
                uuid.uuid4(),
                True,
            ),
        ) as persist_mock,
    ):
        result = (
            persist_topology_shift_snapshots_for_event(
                db=db,
                event=event,
            )
        )

    assert result.organization_id == (
        organization_id
    )
    assert result.event_id == event_id
    assert result.observed_at == observed_at

    assert result.previous_event_id == (
        comparison.previous_event_id
    )

    assert result.entity_count_delta == 1
    assert result.active_edge_count_delta == 1

    assert result.created == 1
    assert result.existing == 0
    assert len(result.snapshot_ids) == 1

    persist_mock.assert_called_once()


def test_persistence_rejects_comparison_tenant_mismatch():
    organization_id = uuid.uuid4()
    event_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    observed_at = datetime(
        2026, 8, 30, 10, 0,
        tzinfo=timezone.utc,
    )

    event = _event(
        event_id=event_id,
        organization_id=organization_id,
        detected_at=observed_at,
    )

    comparison = _comparison(
        organization_id=uuid.uuid4(),
        event_id=event_id,
        observed_at=observed_at,
        entity_id=entity_id,
    )

    with patch(
        "src.services."
        "topology_shift_snapshot_service."
        "compare_event_topology",
        return_value=comparison,
    ):
        with pytest.raises(
            RuntimeError,
            match="organization",
        ):
            persist_topology_shift_snapshots_for_event(
                db=MagicMock(),
                event=event,
            )


def test_persistence_rejects_current_event_mismatch():
    organization_id = uuid.uuid4()
    event_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    observed_at = datetime(
        2026, 8, 30, 10, 0,
        tzinfo=timezone.utc,
    )

    event = _event(
        event_id=event_id,
        organization_id=organization_id,
        detected_at=observed_at,
    )

    comparison = _comparison(
        organization_id=organization_id,
        event_id=uuid.uuid4(),
        observed_at=observed_at,
        entity_id=entity_id,
    )

    with patch(
        "src.services."
        "topology_shift_snapshot_service."
        "compare_event_topology",
        return_value=comparison,
    ):
        with pytest.raises(
            RuntimeError,
            match="current event",
        ):
            persist_topology_shift_snapshots_for_event(
                db=MagicMock(),
                event=event,
            )


def test_persistence_rejects_observation_time_mismatch():
    organization_id = uuid.uuid4()
    event_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    observed_at = datetime(
        2026, 8, 30, 10, 0,
        tzinfo=timezone.utc,
    )

    event = _event(
        event_id=event_id,
        organization_id=organization_id,
        detected_at=observed_at,
    )

    comparison = _comparison(
        organization_id=organization_id,
        event_id=event_id,
        observed_at=datetime(
            2026, 8, 30, 11, 0,
            tzinfo=timezone.utc,
        ),
        entity_id=entity_id,
    )

    with patch(
        "src.services."
        "topology_shift_snapshot_service."
        "compare_event_topology",
        return_value=comparison,
    ):
        with pytest.raises(
            RuntimeError,
            match="observation time",
        ):
            persist_topology_shift_snapshots_for_event(
                db=MagicMock(),
                event=event,
            )