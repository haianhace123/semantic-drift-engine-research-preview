from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from src.services.anomaly_detection_service import (
    DetectedAnomaly,
)
from src.services.anomaly_persistence_service import (
    AnomalyPersistenceValidationError,
    _as_uuid,
    _resolve_detection_method,
    build_anomaly_detection_key,
)


BASE = datetime(
    2026,
    9,
    1,
    12,
    0,
    tzinfo=timezone.utc,
)


def _uuid(
    value: int,
) -> uuid.UUID:
    return uuid.UUID(
        int=value
    )


def _anomaly(
    *,
    event_id=None,
    entity_id=None,
    page_id=None,
    anomaly_type: str = "activity_burst",
    detection_method: str = "deterministic_activity_burst_v1",
) -> DetectedAnomaly:
    return DetectedAnomaly(
        anomaly_type=anomaly_type,
        event_id=(
            event_id
            if event_id is not None
            else _uuid(1)
        ),
        entity_id=(
            entity_id
            if entity_id is not None
            else _uuid(2)
        ),
        monitored_page_id=(
            page_id
            if page_id is not None
            else _uuid(3)
        ),
        expected_value=1.0,
        actual_value=4.0,
        deviation_score=3.0,
        significance=0.85,
        baseline_days=28,
        window_days=7,
        historical_event_count=4,
        current_event_count=4,
        first_current_event_at=BASE,
        last_current_event_at=BASE,
        detection_method=detection_method,
    )


def test_as_uuid_accepts_uuid() -> None:
    value = _uuid(
        10
    )

    assert (
        _as_uuid(
            value,
            field_name="value",
        )
        == value
    )


def test_as_uuid_accepts_uuid_string() -> None:
    value = _uuid(
        11
    )

    assert (
        _as_uuid(
            str(value),
            field_name="value",
        )
        == value
    )


def test_as_uuid_rejects_invalid_value() -> None:
    with pytest.raises(
        AnomalyPersistenceValidationError,
        match="value must be a valid UUID",
    ):
        _as_uuid(
            "invalid",
            field_name="value",
        )


def test_detection_key_is_sha256_hex() -> None:
    key = build_anomaly_detection_key(
        _anomaly()
    )

    assert len(
        key
    ) == 64

    int(
        key,
        16,
    )


def test_detection_key_is_deterministic() -> None:
    anomaly = _anomaly()

    assert (
        build_anomaly_detection_key(
            anomaly
        )
        == build_anomaly_detection_key(
            anomaly
        )
    )


@pytest.mark.parametrize(
    "changed",
    (
        "event",
        "entity",
        "page",
        "type",
        "method",
    ),
)
def test_detection_key_changes_with_identity_material(
    changed,
) -> None:
    original = _anomaly()

    kwargs = {}

    if changed == "event":
        kwargs[
            "event_id"
        ] = _uuid(
            100
        )

    elif changed == "entity":
        kwargs[
            "entity_id"
        ] = _uuid(
            101
        )

    elif changed == "page":
        kwargs[
            "page_id"
        ] = _uuid(
            102
        )

    elif changed == "type":
        kwargs[
            "anomaly_type"
        ] = "different_type"

    elif changed == "method":
        kwargs[
            "detection_method"
        ] = (
            "deterministic_activity_burst_v2"
        )

    changed_anomaly = _anomaly(
        **kwargs
    )

    assert (
        build_anomaly_detection_key(
            original
        )
        != build_anomaly_detection_key(
            changed_anomaly
        )
    )


def test_detection_key_accepts_uuid_strings() -> None:
    anomaly = _anomaly(
        event_id=str(
            _uuid(20)
        ),
        entity_id=str(
            _uuid(21)
        ),
        page_id=str(
            _uuid(22)
        ),
    )

    key = build_anomaly_detection_key(
        anomaly
    )

    assert len(
        key
    ) == 64


def test_detection_key_rejects_invalid_event_uuid() -> None:
    anomaly = _anomaly(
        event_id="bad-event-id"
    )

    with pytest.raises(
        AnomalyPersistenceValidationError,
        match=(
            "anomaly.event_id must be "
            "a valid UUID"
        ),
    ):
        build_anomaly_detection_key(
            anomaly
        )


def test_detection_key_rejects_invalid_entity_uuid() -> None:
    anomaly = _anomaly(
        entity_id="bad-entity-id"
    )

    with pytest.raises(
        AnomalyPersistenceValidationError,
        match=(
            "anomaly.entity_id must be "
            "a valid UUID"
        ),
    ):
        build_anomaly_detection_key(
            anomaly
        )


def test_detection_key_rejects_invalid_page_uuid() -> None:
    anomaly = _anomaly(
        page_id="bad-page-id"
    )

    with pytest.raises(
        AnomalyPersistenceValidationError,
        match=(
            "anomaly.monitored_page_id must be "
            "a valid UUID"
        ),
    ):
        build_anomaly_detection_key(
            anomaly
        )


def test_default_method_uses_anomaly_provenance() -> None:
    anomaly = _anomaly(
        detection_method=(
            "deterministic_activity_burst_v9"
        )
    )

    assert (
        _resolve_detection_method(
            anomaly,
            None,
        )
        == "deterministic_activity_burst_v9"
    )


def test_explicit_method_overrides_anomaly_provenance() -> None:
    anomaly = _anomaly(
        detection_method=(
            "deterministic_activity_burst_v1"
        )
    )

    assert (
        _resolve_detection_method(
            anomaly,
            "manual_reprocess_v2",
        )
        == "manual_reprocess_v2"
    )


def test_detection_key_uses_explicit_method_override() -> None:
    anomaly = _anomaly(
        detection_method=(
            "deterministic_activity_burst_v1"
        )
    )

    default_key = (
        build_anomaly_detection_key(
            anomaly
        )
    )

    override_key = (
        build_anomaly_detection_key(
            anomaly,
            detection_method=(
                "manual_reprocess_v2"
            ),
        )
    )

    assert (
        default_key
        != override_key
    )


@pytest.mark.parametrize(
    "method",
    (
        "",
        "   ",
    ),
)
def test_empty_anomaly_detection_method_is_rejected(
    method,
) -> None:
    anomaly = _anomaly(
        detection_method=method
    )

    with pytest.raises(
        AnomalyPersistenceValidationError,
        match=(
            "detection_method must not be empty"
        ),
    ):
        build_anomaly_detection_key(
            anomaly
        )


@pytest.mark.parametrize(
    "method",
    (
        "",
        "   ",
    ),
)
def test_empty_explicit_detection_method_is_rejected(
    method,
) -> None:
    anomaly = _anomaly()

    with pytest.raises(
        AnomalyPersistenceValidationError,
        match=(
            "detection_method must not be empty"
        ),
    ):
        build_anomaly_detection_key(
            anomaly,
            detection_method=method,
        )

def test_detection_key_changes_with_baseline_days() -> None:
    original = _anomaly()

    changed = DetectedAnomaly(
        anomaly_type=original.anomaly_type,
        event_id=original.event_id,
        entity_id=original.entity_id,
        monitored_page_id=(
            original.monitored_page_id
        ),
        expected_value=(
            original.expected_value
        ),
        actual_value=(
            original.actual_value
        ),
        deviation_score=(
            original.deviation_score
        ),
        significance=(
            original.significance
        ),
        baseline_days=56,
        window_days=(
            original.window_days
        ),
        historical_event_count=(
            original.historical_event_count
        ),
        current_event_count=(
            original.current_event_count
        ),
        first_current_event_at=(
            original.first_current_event_at
        ),
        last_current_event_at=(
            original.last_current_event_at
        ),
        detection_method=(
            original.detection_method
        ),
    )

    assert (
        build_anomaly_detection_key(
            original
        )
        != build_anomaly_detection_key(
            changed
        )
    )


def test_detection_key_changes_with_window_days() -> None:
    original = _anomaly()

    changed = DetectedAnomaly(
        anomaly_type=original.anomaly_type,
        event_id=original.event_id,
        entity_id=original.entity_id,
        monitored_page_id=(
            original.monitored_page_id
        ),
        expected_value=(
            original.expected_value
        ),
        actual_value=(
            original.actual_value
        ),
        deviation_score=(
            original.deviation_score
        ),
        significance=(
            original.significance
        ),
        baseline_days=(
            original.baseline_days
        ),
        window_days=14,
        historical_event_count=(
            original.historical_event_count
        ),
        current_event_count=(
            original.current_event_count
        ),
        first_current_event_at=(
            original.first_current_event_at
        ),
        last_current_event_at=(
            original.last_current_event_at
        ),
        detection_method=(
            original.detection_method
        ),
    )

    assert (
        build_anomaly_detection_key(
            original
        )
        != build_anomaly_detection_key(
            changed
        )
    )


def test_detection_key_rejects_invalid_baseline_days() -> None:
    original = _anomaly()

    invalid = DetectedAnomaly(
        anomaly_type=original.anomaly_type,
        event_id=original.event_id,
        entity_id=original.entity_id,
        monitored_page_id=(
            original.monitored_page_id
        ),
        expected_value=1.0,
        actual_value=4.0,
        deviation_score=1.0,
        significance=0.8,
        baseline_days=0,
        window_days=7,
        historical_event_count=4,
        current_event_count=4,
        first_current_event_at=BASE,
        last_current_event_at=BASE,
        detection_method=(
            original.detection_method
        ),
    )

    with pytest.raises(
        AnomalyPersistenceValidationError,
        match=(
            "anomaly.baseline_days must "
            "be at least 1"
        ),
    ):
        build_anomaly_detection_key(
            invalid
        )


def test_detection_key_rejects_invalid_window_days() -> None:
    original = _anomaly()

    invalid = DetectedAnomaly(
        anomaly_type=original.anomaly_type,
        event_id=original.event_id,
        entity_id=original.entity_id,
        monitored_page_id=(
            original.monitored_page_id
        ),
        expected_value=1.0,
        actual_value=4.0,
        deviation_score=1.0,
        significance=0.8,
        baseline_days=28,
        window_days=0,
        historical_event_count=4,
        current_event_count=4,
        first_current_event_at=BASE,
        last_current_event_at=BASE,
        detection_method=(
            original.detection_method
        ),
    )

    with pytest.raises(
        AnomalyPersistenceValidationError,
        match=(
            "anomaly.window_days must "
            "be at least 1"
        ),
    ):
        build_anomaly_detection_key(
            invalid
        )