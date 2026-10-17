from __future__ import annotations

import uuid
from datetime import (
    datetime,
    timezone,
)

import pytest

from src.models.semantic_drift_event import (
    SemanticDriftEvent,
)
from src.services.graph_metric_snapshot_service import (
    _validate_event,
)


def test_validate_event_returns_ids():
    organization_id = uuid.uuid4()
    event_id = uuid.uuid4()

    event = SemanticDriftEvent()

    event.id = event_id
    event.organization_id = organization_id
    event.detected_at = datetime(
        2026,
        8,
        29,
        10,
        0,
        tzinfo=timezone.utc,
    )

    result = _validate_event(
        event
    )

    assert result == (
        organization_id,
        event_id,
    )


def test_validate_event_requires_event_id():
    event = SemanticDriftEvent()

    event.organization_id = uuid.uuid4()
    event.detected_at = datetime.now(
        timezone.utc
    )

    with pytest.raises(
        ValueError,
        match="must have an id",
    ):
        _validate_event(
            event
        )


def test_validate_event_requires_organization():
    event = SemanticDriftEvent()

    event.id = uuid.uuid4()
    event.organization_id = None
    event.detected_at = datetime.now(
        timezone.utc
    )

    with pytest.raises(
        ValueError,
        match="organization",
    ):
        _validate_event(
            event
        )


def test_validate_event_requires_detected_at():
    event = SemanticDriftEvent()

    event.id = uuid.uuid4()
    event.organization_id = uuid.uuid4()
    event.detected_at = None

    with pytest.raises(
        ValueError,
        match="detected_at",
    ):
        _validate_event(
            event
        )