from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from src.services.anomaly_detection_service import (
    ACTIVITY_BURST_METHOD_V1,
    ANOMALY_TYPE_ACTIVITY_BURST,
    AnomalyDetectionValidationError,
    AnomalyEventInput,
    detect_activity_burst,
)


def _uuid(value: int) -> uuid.UUID:
    return uuid.UUID(int=value)


def _dt(
    day: int,
    hour: int = 12,
) -> datetime:
    return datetime(
        2026,
        8,
        day,
        hour,
        0,
        tzinfo=timezone.utc,
    )


def _event(
    value: int,
    *,
    page_id: uuid.UUID | None = None,
    entity_id: uuid.UUID | None = None,
    day: int,
    hour: int = 12,
) -> AnomalyEventInput:
    return AnomalyEventInput(
        event_id=_uuid(value),
        entity_id=(
            entity_id
            if entity_id is not None
            else _uuid(100)
        ),
        monitored_page_id=(
            page_id
            if page_id is not None
            else _uuid(200)
        ),
        occurred_at=_dt(
            day,
            hour,
        ),
    )


def test_detects_activity_burst_at_exact_ratio_threshold() -> None:
    historical = (
        _event(
            1,
            day=1,
        ),
        _event(
            2,
            day=5,
        ),
        _event(
            3,
            day=10,
        ),
        _event(
            4,
            day=15,
        ),
        _event(
            5,
            day=20,
        ),
        _event(
            6,
            day=24,
        ),
        _event(
            7,
            day=25,
        ),
        _event(
            8,
            day=26,
        ),
    )

    current = (
        _event(
            20,
            day=27,
        ),
        _event(
            21,
            day=28,
        ),
        _event(
            22,
            day=29,
        ),
        _event(
            23,
            day=30,
        ),
    )

    anomaly = detect_activity_burst(
        historical_events=historical,
        current_events=current,
        baseline_days=28,
        window_days=7,
    )

    assert anomaly is not None

    assert (
        anomaly.anomaly_type
        == ANOMALY_TYPE_ACTIVITY_BURST
    )

    assert (
        anomaly.detection_method
        == ACTIVITY_BURST_METHOD_V1
    )

    assert anomaly.expected_value == 2.0
    assert anomaly.actual_value == 4.0
    assert anomaly.historical_event_count == 8
    assert anomaly.current_event_count == 4

    assert anomaly.entity_id == _uuid(100)
    assert anomaly.monitored_page_id == _uuid(200)

    assert anomaly.event_id == _uuid(23)

    assert anomaly.first_current_event_at == _dt(27)
    assert anomaly.last_current_event_at == _dt(30)


def test_below_ratio_threshold_is_not_anomaly() -> None:
    historical = tuple(
        _event(
            value,
            day=value,
        )
        for value in range(
            1,
            9,
        )
    )

    current = (
        _event(
            20,
            day=27,
        ),
        _event(
            21,
            day=28,
        ),
        _event(
            22,
            day=29,
        ),
    )

    anomaly = detect_activity_burst(
        historical_events=historical,
        current_events=current,
        baseline_days=28,
        window_days=7,
        minimum_ratio=2.0,
    )

    assert anomaly is None


def test_requires_minimum_historical_evidence() -> None:
    historical = (
        _event(
            1,
            day=1,
        ),
        _event(
            2,
            day=2,
        ),
        _event(
            3,
            day=3,
        ),
    )

    current = (
        _event(
            10,
            day=27,
        ),
        _event(
            11,
            day=28,
        ),
        _event(
            12,
            day=29,
        ),
        _event(
            13,
            day=30,
        ),
    )

    assert (
        detect_activity_burst(
            historical_events=historical,
            current_events=current,
        )
        is None
    )


def test_requires_minimum_current_evidence() -> None:
    historical = (
        _event(
            1,
            day=1,
        ),
        _event(
            2,
            day=2,
        ),
        _event(
            3,
            day=3,
        ),
        _event(
            4,
            day=4,
        ),
    )

    current = (
        _event(
            10,
            day=29,
        ),
        _event(
            11,
            day=30,
        ),
    )

    assert (
        detect_activity_burst(
            historical_events=historical,
            current_events=current,
        )
        is None
    )


def test_latest_current_event_is_deterministic_anchor() -> None:
    historical = (
        _event(
            1,
            day=1,
        ),
        _event(
            2,
            day=2,
        ),
        _event(
            3,
            day=3,
        ),
        _event(
            4,
            day=4,
        ),
    )

    current = (
        _event(
            30,
            day=30,
            hour=15,
        ),
        _event(
            20,
            day=29,
        ),
        _event(
            25,
            day=30,
            hour=8,
        ),
    )

    anomaly = detect_activity_burst(
        historical_events=historical,
        current_events=current,
    )

    assert anomaly is not None
    assert anomaly.event_id == _uuid(30)
    assert anomaly.first_current_event_at == _dt(29)
    assert anomaly.last_current_event_at == _dt(
        30,
        15,
    )


def test_same_timestamp_uses_event_id_for_deterministic_order() -> None:
    historical = (
        _event(
            1,
            day=1,
        ),
        _event(
            2,
            day=2,
        ),
        _event(
            3,
            day=3,
        ),
        _event(
            4,
            day=4,
        ),
    )

    current = (
        _event(
            21,
            day=30,
        ),
        _event(
            23,
            day=30,
        ),
        _event(
            22,
            day=30,
        ),
    )

    anomaly = detect_activity_burst(
        historical_events=historical,
        current_events=current,
    )

    assert anomaly is not None
    assert anomaly.event_id == _uuid(23)


def test_rejects_mixed_monitored_pages() -> None:
    historical = (
        _event(
            1,
            page_id=_uuid(200),
            day=1,
        ),
        _event(
            2,
            page_id=_uuid(200),
            day=2,
        ),
        _event(
            3,
            page_id=_uuid(200),
            day=3,
        ),
        _event(
            4,
            page_id=_uuid(200),
            day=4,
        ),
    )

    current = (
        _event(
            10,
            page_id=_uuid(200),
            day=28,
        ),
        _event(
            11,
            page_id=_uuid(201),
            day=29,
        ),
        _event(
            12,
            page_id=_uuid(200),
            day=30,
        ),
    )

    with pytest.raises(
        AnomalyDetectionValidationError,
        match="exactly one monitored page",
    ):
        detect_activity_burst(
            historical_events=historical,
            current_events=current,
        )


def test_rejects_mixed_entities() -> None:
    historical = (
        _event(
            1,
            entity_id=_uuid(100),
            day=1,
        ),
        _event(
            2,
            entity_id=_uuid(100),
            day=2,
        ),
        _event(
            3,
            entity_id=_uuid(100),
            day=3,
        ),
        _event(
            4,
            entity_id=_uuid(100),
            day=4,
        ),
    )

    current = (
        _event(
            10,
            entity_id=_uuid(100),
            day=28,
        ),
        _event(
            11,
            entity_id=_uuid(101),
            day=29,
        ),
        _event(
            12,
            entity_id=_uuid(100),
            day=30,
        ),
    )

    with pytest.raises(
        AnomalyDetectionValidationError,
        match="exactly one entity",
    ):
        detect_activity_burst(
            historical_events=historical,
            current_events=current,
        )


def test_empty_evidence_returns_none() -> None:
    assert (
        detect_activity_burst(
            historical_events=(),
            current_events=(),
        )
        is None
    )


@pytest.mark.parametrize(
    (
        "kwargs",
        "message",
    ),
    (
        (
            {
                "baseline_days": 0,
            },
            "baseline_days",
        ),
        (
            {
                "window_days": 0,
            },
            "window_days",
        ),
        (
            {
                "minimum_historical_events": 0,
            },
            "minimum_historical_events",
        ),
        (
            {
                "minimum_current_events": 0,
            },
            "minimum_current_events",
        ),
        (
            {
                "minimum_ratio": 1.0,
            },
            "minimum_ratio",
        ),
    ),
)
def test_rejects_invalid_configuration(
    kwargs,
    message,
) -> None:
    with pytest.raises(
        AnomalyDetectionValidationError,
        match=message,
    ):
        detect_activity_burst(
            historical_events=(),
            current_events=(),
            **kwargs,
        )


def test_scores_are_bounded_and_reproducible() -> None:
    historical = (
        _event(
            1,
            day=1,
        ),
        _event(
            2,
            day=2,
        ),
        _event(
            3,
            day=3,
        ),
        _event(
            4,
            day=4,
        ),
    )

    current = tuple(
        _event(
            100 + index,
            day=28 + (
                index
                % 3
            ),
            hour=index,
        )
        for index in range(
            10
        )
    )

    first = detect_activity_burst(
        historical_events=historical,
        current_events=current,
    )

    second = detect_activity_burst(
        historical_events=historical,
        current_events=current,
    )

    assert first is not None
    assert first == second

    assert (
        0.0
        <= first.deviation_score
        <= 1.0
    )

    assert (
        0.0
        <= first.significance
        <= 1.0
    )