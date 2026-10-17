from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.services.graph_service import (
    GraphPersistenceResult,
    _as_uuid,
    _event_seen_at,
)


def test_as_uuid_returns_uuid_unchanged():
    value = uuid.uuid4()

    assert _as_uuid(value) == value


def test_as_uuid_converts_string():
    value = uuid.uuid4()

    assert _as_uuid(str(value)) == value


def test_as_uuid_rejects_invalid_value():
    with pytest.raises(ValueError):
        _as_uuid("not-a-uuid")


def test_event_seen_at_uses_detected_at():
    detected_at = datetime(
        2026,
        8,
        28,
        8,
        0,
        tzinfo=timezone.utc,
    )

    event = SimpleNamespace(
        detected_at=detected_at,
    )

    assert _event_seen_at(event) == detected_at


def test_graph_persistence_result_is_immutable():
    result = GraphPersistenceResult(
        created=1,
        updated=2,
        skipped=3,
        relationship_ids=("a", "b"),
    )

    assert result.created == 1
    assert result.updated == 2
    assert result.skipped == 3
    assert result.relationship_ids == (
        "a",
        "b",
    )

    with pytest.raises(
        AttributeError,
    ):
        result.created = 99