from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from src.models.pipeline_checkpoint import (
    PIPELINE_ALERT_GENERATION_V1,
    PIPELINE_PATTERN_DETECTION_V1,
)
from src.services.intelligence_pipeline_service import (
    IntelligencePipelineExecutionError,
    execute_intelligence_pipeline,
)
from src.services.pipeline_source_discovery_service import (
    PipelineSourceDiscovery,
)


UTC = timezone.utc


@pytest.fixture(autouse=True)
def _stub_organization_execution_lock(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "acquire_organization_execution_lock",
        lambda db, **kwargs: None,
    )


def _source(
    *,
    pipeline_name=PIPELINE_PATTERN_DETECTION_V1,
):
    return PipelineSourceDiscovery(
        organization_id=uuid.uuid4(),
        pipeline_name=pipeline_name,
        source_created_at=datetime(
            2026,
            9,
            11,
            4,
            0,
            tzinfo=UTC,
        ),
        source_record_id=uuid.uuid4(),
        temporal_cursor=datetime(
            2026,
            9,
            10,
            23,
            0,
            tzinfo=UTC,
        ),
    )


def _pattern_result():
    persisted = (
        SimpleNamespace(
            created=True
        ),
        SimpleNamespace(
            created=False
        ),
    )

    return SimpleNamespace(
        detected_count=2,
        created_count=1,
        existing_count=1,
        persisted_patterns=persisted,
    )


def _pattern_checkpoint():
    return SimpleNamespace(
        advanced=True
    )


def _anomaly_result():
    return SimpleNamespace(
        detected_count=1,
        created_count=1,
        existing_count=0,
    )


def _alert_result():
    return SimpleNamespace(
        alert_created_count=2,
        alert_existing_count=0,
        checkpoint=SimpleNamespace(
            advanced=True
        ),
    )


def test_pipeline_executes_pattern_anomaly_alert_in_order(
    monkeypatch,
):
    source = _source()
    calls = []

    pattern_result = _pattern_result()
    pattern_checkpoint = _pattern_checkpoint()
    anomaly_result = _anomaly_result()
    alert_result = _alert_result()

    def fake_pattern(
        db,
        **kwargs,
    ):
        calls.append(
            (
                "pattern",
                db,
                kwargs,
            )
        )
        return pattern_result

    def fake_checkpoint(
        db,
        **kwargs,
    ):
        calls.append(
            (
                "pattern_checkpoint",
                db,
                kwargs,
            )
        )
        return pattern_checkpoint

    def fake_anomaly(
        db,
        **kwargs,
    ):
        calls.append(
            (
                "anomaly",
                db,
                kwargs,
            )
        )
        return anomaly_result

    def fake_alert(
        db,
        *,
        source,
    ):
        calls.append(
            (
                "alert",
                db,
                source,
            )
        )
        return alert_result

    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_pattern_detection",
        fake_pattern,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "persist_pipeline_checkpoint",
        fake_checkpoint,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_anomaly_detection",
        fake_anomaly,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_alert_generation",
        fake_alert,
    )

    db = object()

    result = execute_intelligence_pipeline(
        db,
        source=source,
    )

    assert [
        call[0]
        for call in calls
    ] == [
        "pattern",
        "pattern_checkpoint",
        "anomaly",
        "alert",
    ]

    pattern_call = calls[0]

    assert pattern_call[1] is db
    assert pattern_call[2] == {
        "organization_id":
            source.organization_id,
        "as_of":
            source.temporal_cursor,
        "knowledge_cutoff":
            source.source_created_at,
        "knowledge_cutoff_record_id":
            source.source_record_id,
    }

    checkpoint_call = calls[1]

    assert checkpoint_call[1] is db
    assert checkpoint_call[2] == {
        "organization_id":
            source.organization_id,
        "pipeline_name":
            PIPELINE_PATTERN_DETECTION_V1,
        "source_created_at":
            source.source_created_at,
        "source_record_id":
            source.source_record_id,
        "knowledge_cursor":
            source.temporal_cursor,
        "metadata_json": {
            "detected_count": 2,
            "created_count": 1,
            "existing_count": 1,
        },
    }

    anomaly_call = calls[2]

    assert anomaly_call[1] is db
    assert anomaly_call[2] == {
        "organization_id":
            source.organization_id,
        "as_of":
            source.temporal_cursor,
    }

    alert_call = calls[3]

    assert alert_call[1] is db

    alert_source = alert_call[2]

    assert (
        alert_source.pipeline_name
        == PIPELINE_ALERT_GENERATION_V1
    )
    assert (
        alert_source.organization_id
        == source.organization_id
    )
    assert (
        alert_source.source_created_at
        == source.source_created_at
    )
    assert (
        alert_source.source_record_id
        == source.source_record_id
    )
    assert (
        alert_source.temporal_cursor
        == source.temporal_cursor
    )

    assert result.organization_id == source.organization_id
    assert result.source_created_at == source.source_created_at
    assert result.source_record_id == source.source_record_id
    assert result.temporal_cursor == source.temporal_cursor
    assert result.pattern is pattern_result
    assert result.pattern_checkpoint is pattern_checkpoint
    assert result.anomaly is anomaly_result
    assert result.alert is alert_result


def test_pipeline_rejects_non_pattern_source():
    source = _source(
        pipeline_name=PIPELINE_ALERT_GENERATION_V1,
    )

    with pytest.raises(
        IntelligencePipelineExecutionError,
        match="pattern_detection_v1",
    ):
        execute_intelligence_pipeline(
            object(),
            source=source,
        )


def test_pattern_failure_stops_all_downstream_stages(
    monkeypatch,
):
    source = _source()
    calls = []

    def fail_pattern(
        db,
        **kwargs,
    ):
        calls.append("pattern")
        raise RuntimeError(
            "pattern failed"
        )

    def unexpected(
        *args,
        **kwargs,
    ):
        calls.append("unexpected")
        raise AssertionError(
            "downstream stage must not run"
        )

    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_pattern_detection",
        fail_pattern,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "persist_pipeline_checkpoint",
        unexpected,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_anomaly_detection",
        unexpected,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_alert_generation",
        unexpected,
    )

    with pytest.raises(
        RuntimeError,
        match="pattern failed",
    ):
        execute_intelligence_pipeline(
            object(),
            source=source,
        )

    assert calls == [
        "pattern",
    ]


def test_pattern_checkpoint_failure_stops_anomaly_and_alert(
    monkeypatch,
):
    source = _source()
    calls = []

    def fake_pattern(
        db,
        **kwargs,
    ):
        calls.append("pattern")
        return _pattern_result()

    def fail_checkpoint(
        db,
        **kwargs,
    ):
        calls.append(
            "pattern_checkpoint"
        )
        raise RuntimeError(
            "checkpoint failed"
        )

    def unexpected(
        *args,
        **kwargs,
    ):
        calls.append("unexpected")
        raise AssertionError(
            "downstream stage must not run"
        )

    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_pattern_detection",
        fake_pattern,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "persist_pipeline_checkpoint",
        fail_checkpoint,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_anomaly_detection",
        unexpected,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_alert_generation",
        unexpected,
    )

    with pytest.raises(
        RuntimeError,
        match="checkpoint failed",
    ):
        execute_intelligence_pipeline(
            object(),
            source=source,
        )

    assert calls == [
        "pattern",
        "pattern_checkpoint",
    ]


def test_anomaly_failure_prevents_alert_execution(
    monkeypatch,
):
    source = _source()
    calls = []

    def fake_pattern(
        db,
        **kwargs,
    ):
        calls.append("pattern")
        return _pattern_result()

    def fake_checkpoint(
        db,
        **kwargs,
    ):
        calls.append(
            "pattern_checkpoint"
        )
        return _pattern_checkpoint()

    def fail_anomaly(
        db,
        **kwargs,
    ):
        calls.append("anomaly")
        raise RuntimeError(
            "anomaly failed"
        )

    def unexpected_alert(
        *args,
        **kwargs,
    ):
        calls.append("alert")
        raise AssertionError(
            "alert must not run"
        )

    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_pattern_detection",
        fake_pattern,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "persist_pipeline_checkpoint",
        fake_checkpoint,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_anomaly_detection",
        fail_anomaly,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_alert_generation",
        unexpected_alert,
    )

    with pytest.raises(
        RuntimeError,
        match="anomaly failed",
    ):
        execute_intelligence_pipeline(
            object(),
            source=source,
        )

    assert calls == [
        "pattern",
        "pattern_checkpoint",
        "anomaly",
    ]


def test_alert_failure_occurs_only_after_upstream_stages_complete(
    monkeypatch,
):
    source = _source()
    calls = []

    def fake_pattern(
        db,
        **kwargs,
    ):
        calls.append("pattern")
        return _pattern_result()

    def fake_checkpoint(
        db,
        **kwargs,
    ):
        calls.append(
            "pattern_checkpoint"
        )
        return _pattern_checkpoint()

    def fake_anomaly(
        db,
        **kwargs,
    ):
        calls.append("anomaly")
        return _anomaly_result()

    def fail_alert(
        db,
        *,
        source,
    ):
        calls.append("alert")
        raise RuntimeError(
            "alert failed"
        )

    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_pattern_detection",
        fake_pattern,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "persist_pipeline_checkpoint",
        fake_checkpoint,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_anomaly_detection",
        fake_anomaly,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_alert_generation",
        fail_alert,
    )

    with pytest.raises(
        RuntimeError,
        match="alert failed",
    ):
        execute_intelligence_pipeline(
            object(),
            source=source,
        )

    assert calls == [
        "pattern",
        "pattern_checkpoint",
        "anomaly",
        "alert",
    ]


def test_pipeline_does_not_persist_anomaly_checkpoint(
    monkeypatch,
):
    source = _source()
    checkpoint_pipeline_names = []

    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_pattern_detection",
        lambda db, **kwargs: _pattern_result(),
    )

    def fake_checkpoint(
        db,
        **kwargs,
    ):
        checkpoint_pipeline_names.append(
            kwargs["pipeline_name"]
        )
        return _pattern_checkpoint()

    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "persist_pipeline_checkpoint",
        fake_checkpoint,
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_anomaly_detection",
        lambda db, **kwargs: _anomaly_result(),
    )
    monkeypatch.setattr(
        "src.services.intelligence_pipeline_service."
        "execute_alert_generation",
        lambda db, **kwargs: _alert_result(),
    )

    execute_intelligence_pipeline(
        object(),
        source=source,
    )

    assert checkpoint_pipeline_names == [
        PIPELINE_PATTERN_DETECTION_V1,
    ]