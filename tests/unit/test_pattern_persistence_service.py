from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from src.services.pattern_detection_service import DetectedPattern
from src.services.pattern_persistence_service import (
    PatternPersistenceValidationError,
    build_pattern_detection_key,
)


def _uuid(value: int) -> uuid.UUID:
    return uuid.UUID(int=value)


def _dt(
    day: int,
    hour: int = 0,
) -> datetime:
    return datetime(
        2026,
        8,
        day,
        hour,
        tzinfo=timezone.utc,
    )


def _pattern(
    *,
    pattern_type: str = "repeated_drift",
    entity_id: uuid.UUID | None = None,
    event_ids: tuple[uuid.UUID, ...] | None = None,
) -> DetectedPattern:
    resolved_entity_id = (
        entity_id
        if entity_id is not None
        else _uuid(100)
    )

    resolved_event_ids = (
        event_ids
        if event_ids is not None
        else (
            _uuid(1),
            _uuid(2),
            _uuid(3),
        )
    )

    return DetectedPattern(
        pattern_type=pattern_type,
        entity_id=resolved_entity_id,
        event_ids=resolved_event_ids,
        first_event_at=_dt(1),
        last_event_at=_dt(3),
        event_count=len(resolved_event_ids),
        significance_score=0.75,
        detection_method="deterministic_repeated_drift_v1",
    )


def test_detection_key_is_sha256_hex() -> None:
    key = build_pattern_detection_key(
        _pattern()
    )

    assert len(key) == 64
    assert all(
        character in "0123456789abcdef"
        for character in key
    )


def test_detection_key_is_deterministic() -> None:
    pattern = _pattern()

    first = build_pattern_detection_key(
        pattern
    )
    second = build_pattern_detection_key(
        pattern
    )

    assert first == second


def test_detection_key_changes_with_pattern_type() -> None:
    first = build_pattern_detection_key(
        _pattern(
            pattern_type="repeated_drift",
        )
    )
    second = build_pattern_detection_key(
        _pattern(
            pattern_type="risk_escalation",
        )
    )

    assert first != second


def test_detection_key_changes_with_entity() -> None:
    first = build_pattern_detection_key(
        _pattern(
            entity_id=_uuid(100),
        )
    )
    second = build_pattern_detection_key(
        _pattern(
            entity_id=_uuid(101),
        )
    )

    assert first != second


def test_detection_key_changes_with_event_sequence() -> None:
    first = build_pattern_detection_key(
        _pattern(
            event_ids=(
                _uuid(1),
                _uuid(2),
                _uuid(3),
            ),
        )
    )

    second = build_pattern_detection_key(
        _pattern(
            event_ids=(
                _uuid(1),
                _uuid(3),
                _uuid(2),
            ),
        )
    )

    assert first != second


def test_detection_key_changes_with_detection_method() -> None:
    pattern = _pattern()

    first = build_pattern_detection_key(
        pattern,
        detection_method="deterministic_v1",
    )

    second = build_pattern_detection_key(
        pattern,
        detection_method="deterministic_v2",
    )

    assert first != second


def test_detection_key_accepts_uuid_strings() -> None:
    pattern = _pattern()

    string_pattern = DetectedPattern(
        pattern_type=pattern.pattern_type,
        entity_id=str(pattern.entity_id),
        event_ids=tuple(
            str(event_id)
            for event_id in pattern.event_ids
        ),
        first_event_at=pattern.first_event_at,
        last_event_at=pattern.last_event_at,
        event_count=pattern.event_count,
        significance_score=pattern.significance_score,
        detection_method=pattern.detection_method,
    )

    assert (
        build_pattern_detection_key(
            string_pattern
        )
        == build_pattern_detection_key(
            pattern
        )
    )


def test_detection_key_rejects_empty_event_ids() -> None:
    pattern = _pattern(
        event_ids=(),
    )

    with pytest.raises(
        PatternPersistenceValidationError,
        match="event_ids",
    ):
        build_pattern_detection_key(
            pattern
        )


def test_detection_key_rejects_invalid_entity_uuid() -> None:
    pattern = _pattern()

    invalid = DetectedPattern(
        pattern_type=pattern.pattern_type,
        entity_id="not-a-uuid",
        event_ids=pattern.event_ids,
        first_event_at=pattern.first_event_at,
        last_event_at=pattern.last_event_at,
        event_count=pattern.event_count,
        significance_score=pattern.significance_score,
        detection_method=pattern.detection_method,
    )

    with pytest.raises(
        PatternPersistenceValidationError,
        match="entity_id",
    ):
        build_pattern_detection_key(
            invalid
        )


def test_detection_key_rejects_invalid_event_uuid() -> None:
    pattern = _pattern()

    invalid = DetectedPattern(
        pattern_type=pattern.pattern_type,
        entity_id=pattern.entity_id,
        event_ids=(
            pattern.event_ids[0],
            "not-a-uuid",
        ),
        first_event_at=pattern.first_event_at,
        last_event_at=pattern.last_event_at,
        event_count=2,
        significance_score=pattern.significance_score,
        detection_method=pattern.detection_method,
    )

    with pytest.raises(
        PatternPersistenceValidationError,
        match="event_ids",
    ):
        build_pattern_detection_key(
            invalid
        )


@pytest.mark.parametrize(
    "method",
    [
        "",
        " ",
        "\t",
    ],
)
def test_detection_key_rejects_empty_method(
    method: str,
) -> None:
    with pytest.raises(
        PatternPersistenceValidationError,
        match="detection_method",
    ):
        build_pattern_detection_key(
            _pattern(),
            detection_method=method,
        )


def test_detection_key_defaults_to_pattern_detection_method() -> None:
    pattern = _pattern()

    implicit = build_pattern_detection_key(
        pattern
    )

    explicit = build_pattern_detection_key(
        pattern,
        detection_method=pattern.detection_method,
    )

    assert implicit == explicit


def test_detection_key_changes_when_pattern_method_changes() -> None:
    pattern = _pattern()

    changed_method = DetectedPattern(
        pattern_type=pattern.pattern_type,
        entity_id=pattern.entity_id,
        event_ids=pattern.event_ids,
        first_event_at=pattern.first_event_at,
        last_event_at=pattern.last_event_at,
        event_count=pattern.event_count,
        significance_score=pattern.significance_score,
        detection_method="deterministic_repeated_drift_v2",
    )

    assert (
        build_pattern_detection_key(
            pattern
        )
        != build_pattern_detection_key(
            changed_method
        )
    )