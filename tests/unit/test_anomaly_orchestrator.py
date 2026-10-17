from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)
import uuid

import pytest

import src.services.anomaly_orchestrator as module
from src.services.anomaly_detection_service import (
    ACTIVITY_BURST_METHOD_V1,
    AnomalyEventInput,
    DetectedAnomaly,
)
from src.services.anomaly_evidence_service import (
    AnomalyEvidenceBundle,
    AnomalyEvidenceValidationError,
    PageAnomalyEvidence,
)
from src.services.anomaly_orchestrator import (
    detect_organization_anomalies,
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


def _event(
    value: int,
    *,
    entity_id: uuid.UUID,
    page_id: uuid.UUID,
    occurred_at: datetime,
) -> AnomalyEventInput:
    return AnomalyEventInput(
        event_id=_uuid(
            value
        ),
        entity_id=entity_id,
        monitored_page_id=page_id,
        occurred_at=occurred_at,
    )


def _page(
    *,
    entity_value: int,
    page_value: int,
    historical_count: int,
    current_count: int,
    event_offset: int,
) -> PageAnomalyEvidence:
    entity_id = _uuid(
        entity_value
    )

    page_id = _uuid(
        page_value
    )

    historical = tuple(
        _event(
            event_offset + index,
            entity_id=entity_id,
            page_id=page_id,
            occurred_at=(
                BASE
                - timedelta(
                    days=30 - index
                )
            ),
        )
        for index in range(
            historical_count
        )
    )

    current = tuple(
        _event(
            event_offset
            + 100
            + index,
            entity_id=entity_id,
            page_id=page_id,
            occurred_at=(
                BASE
                - timedelta(
                    days=(
                        current_count
                        - index
                    )
                )
            ),
        )
        for index in range(
            current_count
        )
    )

    return PageAnomalyEvidence(
        monitored_page_id=page_id,
        entity_id=entity_id,
        historical_events=historical,
        current_events=current,
    )


def _bundle(
    pages,
) -> AnomalyEvidenceBundle:
    return AnomalyEvidenceBundle(
        organization_id=_uuid(
            500
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
        pages=tuple(
            pages
        ),
    )


def _detected(
    *,
    event_value: int,
    entity_value: int,
    page_value: int,
    last_at: datetime,
) -> DetectedAnomaly:
    return DetectedAnomaly(
        anomaly_type="activity_burst",
        event_id=_uuid(
            event_value
        ),
        entity_id=_uuid(
            entity_value
        ),
        monitored_page_id=_uuid(
            page_value
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
            last_at
            - timedelta(
                days=2
            )
        ),
        last_current_event_at=last_at,
        detection_method=(
            ACTIVITY_BURST_METHOD_V1
        ),
    )


def test_orchestrator_detects_activity_burst(
    monkeypatch,
) -> None:
    evidence = _bundle(
        [
            _page(
                entity_value=10,
                page_value=20,
                historical_count=4,
                current_count=4,
                event_offset=1000,
            )
        ]
    )

    monkeypatch.setattr(
        module,
        "load_anomaly_evidence",
        lambda *args, **kwargs: evidence,
    )

    result = detect_organization_anomalies(
        object(),
        organization_id=_uuid(
            500
        ),
        as_of=BASE,
    )

    assert (
        result.organization_id
        == evidence.organization_id
    )

    assert result.as_of == BASE
    assert result.baseline_days == 28
    assert result.window_days == 7

    assert (
        result.evidence_historical_count
        == 4
    )

    assert (
        result.evidence_current_count
        == 4
    )

    assert len(
        result.anomalies
    ) == 1

    assert (
        result.anomalies[0].anomaly_type
        == "activity_burst"
    )


def test_orchestrator_skips_non_anomalous_page(
    monkeypatch,
) -> None:
    evidence = _bundle(
        [
            _page(
                entity_value=10,
                page_value=20,
                historical_count=8,
                current_count=3,
                event_offset=2000,
            )
        ]
    )

    monkeypatch.setattr(
        module,
        "load_anomaly_evidence",
        lambda *args, **kwargs: evidence,
    )

    result = detect_organization_anomalies(
        object(),
        organization_id=_uuid(
            500
        ),
        as_of=BASE,
    )

    assert result.anomalies == ()


def test_orchestrator_processes_pages_independently(
    monkeypatch,
) -> None:
    evidence = _bundle(
        [
            _page(
                entity_value=10,
                page_value=20,
                historical_count=4,
                current_count=4,
                event_offset=3000,
            ),
            _page(
                entity_value=11,
                page_value=21,
                historical_count=8,
                current_count=3,
                event_offset=4000,
            ),
        ]
    )

    monkeypatch.setattr(
        module,
        "load_anomaly_evidence",
        lambda *args, **kwargs: evidence,
    )

    result = detect_organization_anomalies(
        object(),
        organization_id=_uuid(
            500
        ),
        as_of=BASE,
    )

    assert len(
        result.anomalies
    ) == 1

    assert (
        result.anomalies[0].monitored_page_id
        == _uuid(
            20
        )
    )


def test_orchestrator_returns_deterministic_order(
    monkeypatch,
) -> None:
    evidence = _bundle(
        []
    )

    first = _detected(
        event_value=100,
        entity_value=10,
        page_value=20,
        last_at=(
            BASE
            - timedelta(
                hours=1
            )
        ),
    )

    second = _detected(
        event_value=101,
        entity_value=11,
        page_value=21,
        last_at=BASE,
    )

    monkeypatch.setattr(
        module,
        "load_anomaly_evidence",
        lambda *args, **kwargs: evidence,
    )

    detected = iter(
        [
            second,
            first,
        ]
    )

    evidence_with_pages = _bundle(
        [
            _page(
                entity_value=10,
                page_value=20,
                historical_count=4,
                current_count=3,
                event_offset=5000,
            ),
            _page(
                entity_value=11,
                page_value=21,
                historical_count=4,
                current_count=3,
                event_offset=6000,
            ),
        ]
    )

    monkeypatch.setattr(
        module,
        "load_anomaly_evidence",
        lambda *args, **kwargs: (
            evidence_with_pages
        ),
    )

    monkeypatch.setattr(
        module,
        "detect_activity_burst",
        lambda **kwargs: next(
            detected
        ),
    )

    result = detect_organization_anomalies(
        object(),
        organization_id=_uuid(
            500
        ),
        as_of=BASE,
    )

    assert result.anomalies == (
        first,
        second,
    )


def test_orchestrator_forwards_loader_configuration(
    monkeypatch,
) -> None:
    calls = {}

    evidence = _bundle(
        []
    )

    def fake_loader(
        db,
        **kwargs,
    ):
        calls["db"] = db
        calls.update(
            kwargs
        )
        return evidence

    monkeypatch.setattr(
        module,
        "load_anomaly_evidence",
        fake_loader,
    )

    db = object()

    detect_organization_anomalies(
        db,
        organization_id=_uuid(
            700
        ),
        as_of=BASE,
        baseline_days=21,
        window_days=5,
    )

    assert calls == {
        "db": db,
        "organization_id": _uuid(
            700
        ),
        "as_of": BASE,
        "baseline_days": 21,
        "window_days": 5,
    }


def test_orchestrator_forwards_detector_thresholds(
    monkeypatch,
) -> None:
    evidence = _bundle(
        [
            _page(
                entity_value=10,
                page_value=20,
                historical_count=4,
                current_count=3,
                event_offset=7000,
            )
        ]
    )

    monkeypatch.setattr(
        module,
        "load_anomaly_evidence",
        lambda *args, **kwargs: evidence,
    )

    calls = {}

    def fake_detector(
        **kwargs,
    ):
        calls.update(
            kwargs
        )
        return None

    monkeypatch.setattr(
        module,
        "detect_activity_burst",
        fake_detector,
    )

    detect_organization_anomalies(
        object(),
        organization_id=_uuid(
            500
        ),
        as_of=BASE,
        minimum_historical_events=6,
        minimum_current_events=4,
        minimum_ratio=2.5,
    )

    assert (
        calls[
            "minimum_historical_events"
        ]
        == 6
    )

    assert (
        calls[
            "minimum_current_events"
        ]
        == 4
    )

    assert (
        calls[
            "minimum_ratio"
        ]
        == 2.5
    )

    assert (
        calls[
            "baseline_days"
        ]
        == 28
    )

    assert (
        calls[
            "window_days"
        ]
        == 7
    )


def test_orchestrator_propagates_evidence_validation(
    monkeypatch,
) -> None:
    def fail_loader(
        *args,
        **kwargs,
    ):
        raise AnomalyEvidenceValidationError(
            "invalid evidence"
        )

    monkeypatch.setattr(
        module,
        "load_anomaly_evidence",
        fail_loader,
    )

    with pytest.raises(
        AnomalyEvidenceValidationError,
        match="invalid evidence",
    ):
        detect_organization_anomalies(
            object(),
            organization_id=_uuid(
                500
            ),
            as_of=BASE,
        )