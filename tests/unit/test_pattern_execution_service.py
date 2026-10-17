from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from src.services.pattern_detection_service import DetectedPattern
from src.services.pattern_execution_service import (
    PatternExecutionResult,
    execute_pattern_detection,
)
from src.services.pattern_orchestrator import PatternDetectionResult
from src.services.pattern_persistence_service import PersistedPatternResult


def _dt() -> datetime:
    return datetime(
        2026,
        8,
        31,
        7,
        0,
        tzinfo=timezone.utc,
    )


def _uuid(value: int) -> uuid.UUID:
    return uuid.UUID(int=value)


def _pattern() -> DetectedPattern:
    return DetectedPattern(
        pattern_type="repeated_drift",
        entity_id=_uuid(100),
        event_ids=(
            _uuid(1),
            _uuid(2),
            _uuid(3),
        ),
        first_event_at=datetime(
            2026,
            8,
            28,
            tzinfo=timezone.utc,
        ),
        last_event_at=datetime(
            2026,
            8,
            30,
            tzinfo=timezone.utc,
        ),
        event_count=3,
        significance_score=0.75,
        detection_method=(
            "deterministic_repeated_drift_v1"
        ),
    )


def _detection(
    *,
    patterns: tuple[DetectedPattern, ...],
) -> PatternDetectionResult:
    return PatternDetectionResult(
        organization_id=_uuid(500),
        as_of=_dt(),
        window_start=datetime(
            2026,
            8,
            1,
            7,
            0,
            tzinfo=timezone.utc,
        ),
        lookback_days=30,
        evidence_event_count=3,
        evidence_risk_count=0,
        evidence_topology_count=0,
        patterns=patterns,
    )


def _persisted(
    *,
    created: bool,
    value: int,
) -> PersistedPatternResult:
    return PersistedPatternResult(
        pattern_id=_uuid(value),
        organization_id=_uuid(500),
        pattern_type="repeated_drift",
        detection_key=f"{value:064x}",
        created=created,
    )


def test_execute_detects_and_persists(
    monkeypatch,
) -> None:
    db = object()
    pattern = _pattern()
    detection = _detection(
        patterns=(pattern,),
    )
    persisted = _persisted(
        created=True,
        value=900,
    )

    calls = {}

    def fake_detect(
        received_db,
        **kwargs,
    ):
        calls["detect_db"] = received_db
        calls["detect_kwargs"] = kwargs
        return detection

    def fake_persist(
        received_db,
        **kwargs,
    ):
        calls["persist_db"] = received_db
        calls["persist_kwargs"] = kwargs
        return (persisted,)

    monkeypatch.setattr(
        "src.services.pattern_execution_service."
        "detect_organization_patterns",
        fake_detect,
    )

    monkeypatch.setattr(
        "src.services.pattern_execution_service."
        "persist_detected_patterns",
        fake_persist,
    )

    result = execute_pattern_detection(
        db,
        organization_id=_uuid(500),
        as_of=_dt(),
    )

    assert isinstance(
        result,
        PatternExecutionResult,
    )

    assert result.organization_id == _uuid(500)
    assert result.as_of == _dt()
    assert result.detection is detection
    assert result.persisted_patterns == (
        persisted,
    )

    assert calls["detect_db"] is db
    assert calls["persist_db"] is db

    assert (
        calls["persist_kwargs"]["organization_id"]
        == detection.organization_id
    )

    assert (
        calls["persist_kwargs"]["patterns"]
        is detection.patterns
    )

    assert (
        calls["persist_kwargs"]["detected_at"]
        == detection.as_of
    )


def test_execute_forwards_detection_configuration(
    monkeypatch,
) -> None:
    db = object()

    detection = _detection(
        patterns=(),
    )

    captured = {}

    def fake_detect(
        received_db,
        **kwargs,
    ):
        captured.update(
            kwargs
        )
        return detection

    def fake_persist(
        received_db,
        **kwargs,
    ):
        return ()

    monkeypatch.setattr(
        "src.services.pattern_execution_service."
        "detect_organization_patterns",
        fake_detect,
    )

    monkeypatch.setattr(
        "src.services.pattern_execution_service."
        "persist_detected_patterns",
        fake_persist,
    )

    knowledge_cutoff = (
        _dt()
        + timedelta(days=7)
    )

    knowledge_cutoff_record_id = _uuid(
        501
    )

    knowledge_cutoff_record_id = _uuid(
        501
    )

    execute_pattern_detection(
        db,
        organization_id=_uuid(500),
        as_of=_dt(),
        knowledge_cutoff=knowledge_cutoff,
        knowledge_cutoff_record_id=(
            knowledge_cutoff_record_id
        ),
        lookback_days=45,
        repeated_drift_minimum_events=4,
        risk_escalation_minimum_events=5,
        risk_escalation_minimum_step=0.08,
        topology_sequence_minimum_events=3,
    )

    assert captured == {
        "organization_id": _uuid(500),
        "as_of": _dt(),
        "knowledge_cutoff": knowledge_cutoff,
        "knowledge_cutoff_record_id": (
            knowledge_cutoff_record_id
        ),
        "lookback_days": 45,
        "repeated_drift_minimum_events": 4,
        "risk_escalation_minimum_events": 5,
        "risk_escalation_minimum_step": 0.08,
        "topology_sequence_minimum_events": 3,
    }


def test_zero_pattern_execution_is_valid(
    monkeypatch,
) -> None:
    detection = _detection(
        patterns=(),
    )

    persist_calls = []

    monkeypatch.setattr(
        "src.services.pattern_execution_service."
        "detect_organization_patterns",
        lambda *args, **kwargs: detection,
    )

    def fake_persist(
        *args,
        **kwargs,
    ):
        persist_calls.append(
            kwargs
        )
        return ()

    monkeypatch.setattr(
        "src.services.pattern_execution_service."
        "persist_detected_patterns",
        fake_persist,
    )

    result = execute_pattern_detection(
        object(),
        organization_id=_uuid(500),
        as_of=_dt(),
    )

    assert result.detected_count == 0
    assert result.created_count == 0
    assert result.existing_count == 0
    assert result.persisted_patterns == ()

    assert len(
        persist_calls
    ) == 1

    assert (
        persist_calls[0]["patterns"]
        == ()
    )


def test_execution_counts_created_and_existing(
    monkeypatch,
) -> None:
    pattern = _pattern()

    detection = _detection(
        patterns=(
            pattern,
            pattern,
            pattern,
        ),
    )

    persisted = (
        _persisted(
            created=True,
            value=901,
        ),
        _persisted(
            created=False,
            value=902,
        ),
        _persisted(
            created=True,
            value=903,
        ),
    )

    monkeypatch.setattr(
        "src.services.pattern_execution_service."
        "detect_organization_patterns",
        lambda *args, **kwargs: detection,
    )

    monkeypatch.setattr(
        "src.services.pattern_execution_service."
        "persist_detected_patterns",
        lambda *args, **kwargs: persisted,
    )

    result = execute_pattern_detection(
        object(),
        organization_id=_uuid(500),
        as_of=_dt(),
    )

    assert result.detected_count == 3
    assert result.created_count == 2
    assert result.existing_count == 1


def test_execution_does_not_commit_or_rollback(
    monkeypatch,
) -> None:
    class FakeSession:
        def commit(self):
            raise AssertionError(
                "execution service must not commit"
            )

        def rollback(self):
            raise AssertionError(
                "execution service must not rollback"
            )

    db = FakeSession()

    detection = _detection(
        patterns=(),
    )

    monkeypatch.setattr(
        "src.services.pattern_execution_service."
        "detect_organization_patterns",
        lambda *args, **kwargs: detection,
    )

    monkeypatch.setattr(
        "src.services.pattern_execution_service."
        "persist_detected_patterns",
        lambda *args, **kwargs: (),
    )

    result = execute_pattern_detection(
        db,
        organization_id=_uuid(500),
        as_of=_dt(),
    )

    assert result.detected_count == 0


def test_persistence_error_propagates(
    monkeypatch,
) -> None:
    detection = _detection(
        patterns=(
            _pattern(),
        ),
    )

    monkeypatch.setattr(
        "src.services.pattern_execution_service."
        "detect_organization_patterns",
        lambda *args, **kwargs: detection,
    )

    def fail_persistence(
        *args,
        **kwargs,
    ):
        raise RuntimeError(
            "persistence failed"
        )

    monkeypatch.setattr(
        "src.services.pattern_execution_service."
        "persist_detected_patterns",
        fail_persistence,
    )

    try:
        execute_pattern_detection(
            object(),
            organization_id=_uuid(500),
            as_of=_dt(),
        )
    except RuntimeError as exc:
        assert str(exc) == "persistence failed"
    else:
        raise AssertionError(
            "persistence error must propagate"
        )