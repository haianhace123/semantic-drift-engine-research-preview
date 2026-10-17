from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)
import uuid

import src.services.anomaly_execution_service as module

from src.services.anomaly_detection_service import (
    ACTIVITY_BURST_METHOD_V1,
    DetectedAnomaly,
)
from src.services.anomaly_orchestrator import (
    AnomalyDetectionResult,
)
from src.services.anomaly_persistence_service import (
    PersistedAnomalyResult,
)
from src.services.anomaly_execution_service import (
    execute_anomaly_detection,
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
    value: int,
) -> DetectedAnomaly:
    return DetectedAnomaly(
        anomaly_type="activity_burst",
        event_id=_uuid(
            100 + value
        ),
        entity_id=_uuid(
            200 + value
        ),
        monitored_page_id=_uuid(
            300 + value
        ),
        expected_value=1.0,
        actual_value=4.0,
        deviation_score=1.0,
        significance=0.8,
        baseline_days=28,
        window_days=7,
        historical_event_count=4,
        current_event_count=4,
        first_current_event_at=(
            BASE
            - timedelta(
                days=2
            )
        ),
        last_current_event_at=BASE,
        detection_method=(
            ACTIVITY_BURST_METHOD_V1
        ),
    )


def _detection(
    anomalies,
) -> AnomalyDetectionResult:
    return AnomalyDetectionResult(
        organization_id=_uuid(
            1
        ),
        as_of=BASE,
        baseline_start=(
            BASE
            - timedelta(
                days=35
            )
        ),
        current_start=(
            BASE
            - timedelta(
                days=7
            )
        ),
        baseline_days=28,
        window_days=7,
        evidence_historical_count=8,
        evidence_current_count=6,
        anomalies=tuple(
            anomalies
        ),
    )


def _persisted(
    value: int,
    *,
    created: bool,
) -> PersistedAnomalyResult:
    return PersistedAnomalyResult(
        anomaly_id=_uuid(
            500 + value
        ),
        organization_id=_uuid(
            1
        ),
        anomaly_type="activity_burst",
        detection_key=(
            f"{value:064x}"
        ),
        created=created,
    )


def test_execution_detects_and_persists(
    monkeypatch,
) -> None:
    first_anomaly = _anomaly(
        1
    )

    second_anomaly = _anomaly(
        2
    )

    detection = _detection(
        [
            first_anomaly,
            second_anomaly,
        ]
    )

    first_persisted = _persisted(
        1,
        created=True,
    )

    second_persisted = _persisted(
        2,
        created=False,
    )

    monkeypatch.setattr(
        module,
        "detect_organization_anomalies",
        lambda *args, **kwargs: detection,
    )

    calls = {}

    def fake_persist(
        db,
        **kwargs,
    ):
        calls["db"] = db
        calls.update(
            kwargs
        )

        return (
            first_persisted,
            second_persisted,
        )

    monkeypatch.setattr(
        module,
        "persist_detected_anomalies",
        fake_persist,
    )

    db = object()

    result = execute_anomaly_detection(
        db,
        organization_id=_uuid(
            1
        ),
        as_of=BASE,
    )

    assert (
        result.organization_id
        == _uuid(1)
    )

    assert (
        result.detected_count
        == 2
    )

    assert (
        result.created_count
        == 1
    )

    assert (
        result.existing_count
        == 1
    )

    assert result.persisted == (
        first_persisted,
        second_persisted,
    )

    assert calls[
        "db"
    ] is db

    assert (
        calls[
            "organization_id"
        ]
        == _uuid(1)
    )

    assert calls[
        "anomalies"
    ] == (
        first_anomaly,
        second_anomaly,
    )

    assert (
        calls[
            "detected_at"
        ]
        == BASE
    )

    assert (
        calls[
            "detection_method"
        ]
        is None
    )


def test_execution_handles_no_detected_anomalies(
    monkeypatch,
) -> None:
    detection = _detection(
        []
    )

    monkeypatch.setattr(
        module,
        "detect_organization_anomalies",
        lambda *args, **kwargs: detection,
    )

    calls = {}

    def fake_persist(
        db,
        **kwargs,
    ):
        calls.update(
            kwargs
        )
        return ()

    monkeypatch.setattr(
        module,
        "persist_detected_anomalies",
        fake_persist,
    )

    result = execute_anomaly_detection(
        object(),
        organization_id=_uuid(
            1
        ),
        as_of=BASE,
    )

    assert (
        result.detected_count
        == 0
    )

    assert (
        result.created_count
        == 0
    )

    assert (
        result.existing_count
        == 0
    )

    assert result.persisted == ()
    assert calls["anomalies"] == ()


def test_execution_forwards_detection_configuration(
    monkeypatch,
) -> None:
    calls = {}

    detection = _detection(
        []
    )

    def fake_detect(
        db,
        **kwargs,
    ):
        calls["db"] = db
        calls.update(
            kwargs
        )
        return detection

    monkeypatch.setattr(
        module,
        "detect_organization_anomalies",
        fake_detect,
    )

    monkeypatch.setattr(
        module,
        "persist_detected_anomalies",
        lambda *args, **kwargs: (),
    )

    db = object()

    execute_anomaly_detection(
        db,
        organization_id=_uuid(
            10
        ),
        as_of=BASE,
        baseline_days=56,
        window_days=14,
        minimum_historical_events=8,
        minimum_current_events=5,
        minimum_ratio=3.0,
    )

    assert calls == {
        "db": db,
        "organization_id": _uuid(
            10
        ),
        "as_of": BASE,
        "baseline_days": 56,
        "window_days": 14,
        "minimum_historical_events": 8,
        "minimum_current_events": 5,
        "minimum_ratio": 3.0,
    }


def test_execution_forwards_detection_method_override(
    monkeypatch,
) -> None:
    detection = _detection(
        [
            _anomaly(
                1
            )
        ]
    )

    monkeypatch.setattr(
        module,
        "detect_organization_anomalies",
        lambda *args, **kwargs: detection,
    )

    calls = {}

    def fake_persist(
        *args,
        **kwargs,
    ):
        calls.update(
            kwargs
        )

        return (
            _persisted(
                1,
                created=True,
            ),
        )

    monkeypatch.setattr(
        module,
        "persist_detected_anomalies",
        fake_persist,
    )

    execute_anomaly_detection(
        object(),
        organization_id=_uuid(
            1
        ),
        as_of=BASE,
        detection_method=(
            "manual_reprocess_v2"
        ),
    )

    assert (
        calls[
            "detection_method"
        ]
        == "manual_reprocess_v2"
    )


def test_execution_preserves_detection_metadata(
    monkeypatch,
) -> None:
    detection = _detection(
        []
    )

    monkeypatch.setattr(
        module,
        "detect_organization_anomalies",
        lambda *args, **kwargs: detection,
    )

    monkeypatch.setattr(
        module,
        "persist_detected_anomalies",
        lambda *args, **kwargs: (),
    )

    result = execute_anomaly_detection(
        object(),
        organization_id=_uuid(
            1
        ),
        as_of=BASE,
    )

    assert (
        result.as_of
        == detection.as_of
    )

    assert (
        result.baseline_start
        == detection.baseline_start
    )

    assert (
        result.current_start
        == detection.current_start
    )

    assert (
        result.baseline_days
        == detection.baseline_days
    )

    assert (
        result.window_days
        == detection.window_days
    )

    assert (
        result.evidence_historical_count
        == detection.evidence_historical_count
    )

    assert (
        result.evidence_current_count
        == detection.evidence_current_count
    )