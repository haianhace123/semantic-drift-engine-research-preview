from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import scripts.vector_worker as vector_worker_module
from scripts.vector_worker import VectorWorker


class FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def _source(
    organization_id: uuid.UUID,
) -> SimpleNamespace:
    return SimpleNamespace(
        organization_id=organization_id,
        pipeline_name=(
            vector_worker_module.PIPELINE_PATTERN_DETECTION_V1
        ),
    )


def _execution_result() -> SimpleNamespace:
    return SimpleNamespace(
        pattern=SimpleNamespace(
            detected_count=2,
            created_count=1,
            existing_count=1,
        ),
        pattern_checkpoint=SimpleNamespace(
            advanced=True,
        ),
        anomaly=SimpleNamespace(
            detected_count=3,
            created_count=2,
            existing_count=1,
        ),
        alert=SimpleNamespace(
            alert_created_count=4,
            alert_existing_count=1,
            checkpoint=SimpleNamespace(
                advanced=True,
            ),
        ),
    )


def _worker() -> VectorWorker:
    return VectorWorker(
        snapshot_limit=1,
        event_limit=1,
        risk_limit=1,
        pattern_limit=5,
        anomaly_limit=3,
        alert_reconciliation_limit=7,
        max_attempts=1,
        process_embeddings=False,
        process_analysis=False,
        process_temporal_invalidation=False,
        process_risk_scoring=False,
        process_pattern_detection=True,
        process_anomaly_detection=True,
    )


def test_intelligence_worker_executes_pipeline_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()
    candidate = SimpleNamespace(
        organization_id=organization_id,
    )
    source = _source(
        organization_id,
    )

    discovery_session = FakeSession()
    processing_session = FakeSession()
    sessions = iter(
        [
            discovery_session,
            processing_session,
        ]
    )

    monkeypatch.setattr(
        vector_worker_module,
        "SessionLocal",
        lambda: next(sessions),
    )

    pending_calls: list[dict[str, object]] = []

    def fake_discover_pending(
        db,
        *,
        pipeline_name,
        limit,
    ):
        pending_calls.append(
            {
                "db": db,
                "pipeline_name": pipeline_name,
                "limit": limit,
            }
        )
        return [candidate]

    monkeypatch.setattr(
        vector_worker_module,
        "discover_pending_pipeline_sources",
        fake_discover_pending,
    )

    source_calls: list[dict[str, object]] = []

    def fake_discover_source(
        db,
        *,
        organization_id,
        pipeline_name,
    ):
        source_calls.append(
            {
                "db": db,
                "organization_id": organization_id,
                "pipeline_name": pipeline_name,
            }
        )
        return source

    monkeypatch.setattr(
        vector_worker_module,
        "discover_pipeline_source",
        fake_discover_source,
    )

    pipeline_calls: list[dict[str, object]] = []

    def fake_execute_pipeline(
        db,
        *,
        source,
    ):
        pipeline_calls.append(
            {
                "db": db,
                "source": source,
            }
        )
        return _execution_result()

    monkeypatch.setattr(
        vector_worker_module,
        "execute_intelligence_pipeline",
        fake_execute_pipeline,
    )

    clear_calls: list[dict[str, object]] = []

    def fake_clear_schedule(
        db,
        *,
        organization_id,
        work_kind,
    ):
        clear_calls.append(
            {
                "db": db,
                "organization_id": organization_id,
                "work_kind": work_kind,
            }
        )

    monkeypatch.setattr(
        vector_worker_module,
        "clear_organization_work_schedule",
        fake_clear_schedule,
    )

    worker = _worker()

    stats = worker.process_intelligence_organizations()

    assert stats.selected == 1
    assert stats.completed == 1
    assert stats.failed == 0
    assert stats.deferred == 0

    assert pending_calls == [
        {
            "db": discovery_session,
            "pipeline_name": (
                vector_worker_module.PIPELINE_PATTERN_DETECTION_V1
            ),
            "limit": 3,
        }
    ]

    assert source_calls == [
        {
            "db": processing_session,
            "organization_id": organization_id,
            "pipeline_name": (
                vector_worker_module.PIPELINE_PATTERN_DETECTION_V1
            ),
        }
    ]

    assert pipeline_calls == [
        {
            "db": processing_session,
            "source": source,
        }
    ]

    assert clear_calls == [
        {
            "db": processing_session,
            "organization_id": organization_id,
            "work_kind": (
                vector_worker_module.WORK_KIND_INTELLIGENCE
            ),
        }
    ]

    assert discovery_session.closed is True
    assert processing_session.committed is True
    assert processing_session.rolled_back is False
    assert processing_session.closed is True


def test_intelligence_worker_rolls_back_when_pipeline_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()
    candidate = SimpleNamespace(
        organization_id=organization_id,
    )
    source = _source(
        organization_id,
    )

    discovery_session = FakeSession()
    processing_session = FakeSession()
    retry_session = FakeSession()
    sessions = iter(
        [
            discovery_session,
            processing_session,
            retry_session,
        ]
    )

    monkeypatch.setattr(
        vector_worker_module,
        "SessionLocal",
        lambda: next(sessions),
    )

    monkeypatch.setattr(
        vector_worker_module,
        "discover_pending_pipeline_sources",
        lambda db, *, pipeline_name, limit: [
            candidate
        ],
    )

    monkeypatch.setattr(
        vector_worker_module,
        "discover_pipeline_source",
        lambda db, *, organization_id, pipeline_name: source,
    )

    def fail_pipeline(
        db,
        *,
        source,
    ):
        raise RuntimeError(
            "downstream intelligence failure"
        )

    monkeypatch.setattr(
        vector_worker_module,
        "execute_intelligence_pipeline",
        fail_pipeline,
    )

    retry_source_calls: list[dict[str, object]] = []

    def fake_discover_retry_source(
        db,
        *,
        organization_id,
        pipeline_name,
    ):
        retry_source_calls.append(
            {
                "db": db,
                "organization_id": organization_id,
                "pipeline_name": pipeline_name,
            }
        )
        return source

    monkeypatch.setattr(
        vector_worker_module,
        "discover_pipeline_source",
        fake_discover_retry_source,
    )

    retry_schedule_calls: list[dict[str, object]] = []

    def fake_schedule_retry(
        db,
        *,
        organization_id,
        work_kind,
        retry_seconds,
    ):
        retry_schedule_calls.append(
            {
                "db": db,
                "organization_id": organization_id,
                "work_kind": work_kind,
                "retry_seconds": retry_seconds,
            }
        )

    monkeypatch.setattr(
        vector_worker_module,
        "schedule_organization_work_retry",
        fake_schedule_retry,
    )

    worker = _worker()

    stats = worker.process_intelligence_organizations()

    assert stats.selected == 1
    assert stats.completed == 0
    assert stats.failed == 1
    assert stats.deferred == 0

    assert discovery_session.closed is True
    assert processing_session.committed is False
    assert processing_session.rolled_back is True
    assert processing_session.closed is True
    assert retry_source_calls == [
        {
            "db": processing_session,
            "organization_id": organization_id,
            "pipeline_name": (
                vector_worker_module.PIPELINE_PATTERN_DETECTION_V1
            ),
        },
        {
            "db": retry_session,
            "organization_id": organization_id,
            "pipeline_name": (
                vector_worker_module.PIPELINE_PATTERN_DETECTION_V1
            ),
        },
    ]

    assert retry_schedule_calls == [
        {
            "db": retry_session,
            "organization_id": organization_id,
            "work_kind": (
                vector_worker_module.WORK_KIND_INTELLIGENCE
            ),
            "retry_seconds": (
                vector_worker_module.ORGANIZATION_WORK_FAILURE_RETRY_SECONDS
            ),
        }
    ]

    assert retry_session.committed is True
    assert retry_session.rolled_back is False
    assert retry_session.closed is True

def test_worker_rejects_invalid_alert_reconciliation_limit() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "alert_reconciliation_limit must be at least 1"
        ),
    ):
        VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            risk_limit=1,
            pattern_limit=1,
            anomaly_limit=1,
            alert_reconciliation_limit=0,
            max_attempts=1,
        )


@pytest.mark.parametrize(
    (
        "process_pattern_detection",
        "process_anomaly_detection",
    ),
    [
        (True, False),
        (False, True),
    ],
)
def test_worker_rejects_split_intelligence_configuration(
    process_pattern_detection: bool,
    process_anomaly_detection: bool,
) -> None:
    with pytest.raises(
        ValueError,
        match=(
            "Pattern detection and anomaly detection "
            "must be enabled or disabled together"
        ),
    ):
        VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            risk_limit=1,
            pattern_limit=1,
            anomaly_limit=1,
            max_attempts=1,
            process_embeddings=False,
            process_analysis=False,
            process_risk_scoring=True,
            process_pattern_detection=(
                process_pattern_detection
            ),
            process_anomaly_detection=(
                process_anomaly_detection
            ),
        )


def test_run_cycle_uses_one_intelligence_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()

    intelligence_stats = (
        vector_worker_module.BatchStats(
            selected=2,
            completed=2,
            failed=0,
            deferred=0,
        )
    )

    calls = 0

    def fake_process_intelligence():
        nonlocal calls
        calls += 1
        return intelligence_stats

    monkeypatch.setattr(
        worker,
        "process_intelligence_organizations",
        fake_process_intelligence,
    )

    alert_reconciliation_stats = (
        vector_worker_module.BatchStats()
    )

    monkeypatch.setattr(
        worker,
        "process_alert_reconciliation_organizations",
        lambda: alert_reconciliation_stats,
    )

    stats = worker.run_cycle()

    assert calls == 1

    assert (
        stats.pattern_organizations
        is intelligence_stats
    )
    assert (
        stats.anomaly_organizations
        is intelligence_stats
    )

    assert (
        stats.alert_reconciliation_organizations
        is alert_reconciliation_stats
    )


def test_run_cycle_skips_alert_reconciliation_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = VectorWorker(
        snapshot_limit=1,
        event_limit=1,
        risk_limit=1,
        pattern_limit=1,
        anomaly_limit=1,
        alert_reconciliation_limit=1,
        max_attempts=1,
        process_embeddings=False,
        process_analysis=False,
        process_temporal_invalidation=False,
        process_risk_scoring=False,
        process_pattern_detection=True,
        process_anomaly_detection=True,
        process_alert_reconciliation=False,
    )

    monkeypatch.setattr(
        worker,
        "process_intelligence_organizations",
        lambda: vector_worker_module.BatchStats(),
    )

    def unexpected_reconciliation():
        raise AssertionError(
            "Alert reconciliation must remain disabled."
        )

    monkeypatch.setattr(
        worker,
        "process_alert_reconciliation_organizations",
        unexpected_reconciliation,
    )

    stats = worker.run_cycle()

    assert (
        stats.alert_reconciliation_organizations
        == vector_worker_module.BatchStats()
    )


def test_alert_reconciliation_can_run_as_only_enabled_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = VectorWorker(
        snapshot_limit=1,
        event_limit=1,
        risk_limit=1,
        pattern_limit=1,
        anomaly_limit=1,
        alert_reconciliation_limit=1,
        max_attempts=1,
        process_embeddings=False,
        process_analysis=False,
        process_temporal_invalidation=False,
        process_risk_scoring=False,
        process_pattern_detection=False,
        process_anomaly_detection=False,
        process_alert_reconciliation=True,
    )

    reconciliation_stats = (
        vector_worker_module.BatchStats(
            selected=1,
            completed=1,
        )
    )

    monkeypatch.setattr(
        worker,
        "process_alert_reconciliation_organizations",
        lambda: reconciliation_stats,
    )

    stats = worker.run_cycle()

    assert (
        stats.alert_reconciliation_organizations
        is reconciliation_stats
    )


def test_alert_reconciliation_worker_executes_pending_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()

    candidate = SimpleNamespace(
        organization_id=organization_id,
    )

    source = SimpleNamespace(
        organization_id=organization_id,
    )

    discovery_session = FakeSession()
    processing_session = FakeSession()

    sessions = iter(
        [
            discovery_session,
            processing_session,
        ]
    )

    monkeypatch.setattr(
        vector_worker_module,
        "SessionLocal",
        lambda: next(sessions),
    )

    pending_calls: list[
        dict[str, object]
    ] = []

    def fake_discover_pending(
        db,
        *,
        limit,
    ):
        pending_calls.append(
            {
                "db": db,
                "limit": limit,
            }
        )

        return [
            candidate
        ]

    monkeypatch.setattr(
        vector_worker_module,
        "discover_pending_alert_reconciliation_sources",
        fake_discover_pending,
    )

    source_calls: list[
        dict[str, object]
    ] = []

    def fake_discover_source(
        db,
        *,
        organization_id,
    ):
        source_calls.append(
            {
                "db": db,
                "organization_id": organization_id,
            }
        )

        return source

    monkeypatch.setattr(
        vector_worker_module,
        "discover_alert_reconciliation_source",
        fake_discover_source,
    )

    pipeline_calls: list[
        dict[str, object]
    ] = []

    result = SimpleNamespace(
        risk_source_count=0,
        pattern_source_count=0,
        anomaly_source_count=0,
        alert_created_count=0,
        alert_existing_count=0,
        checkpoint=SimpleNamespace(
            advanced=False,
        ),
    )

    def fake_execute_alert_generation(
        db,
        *,
        source,
    ):
        pipeline_calls.append(
            {
                "db": db,
                "source": source,
            }
        )

        return result

    monkeypatch.setattr(
        vector_worker_module,
        "execute_alert_generation",
        fake_execute_alert_generation,
    )

    clear_calls: list[dict[str, object]] = []

    def fake_clear_schedule(
        db,
        *,
        organization_id,
        work_kind,
    ):
        clear_calls.append(
            {
                "db": db,
                "organization_id": organization_id,
                "work_kind": work_kind,
            }
        )

    monkeypatch.setattr(
        vector_worker_module,
        "clear_organization_work_schedule",
        fake_clear_schedule,
    )

    worker = _worker()

    stats = (
        worker.process_alert_reconciliation_organizations()
    )

    assert stats.selected == 1
    assert stats.completed == 1
    assert stats.failed == 0
    assert stats.deferred == 0

    assert pending_calls == [
        {
            "db": discovery_session,
            "limit": 7,
        }
    ]

    assert source_calls == [
        {
            "db": processing_session,
            "organization_id": organization_id,
        }
    ]

    assert pipeline_calls == [
        {
            "db": processing_session,
            "source": source,
        }
    ]

    assert clear_calls == [
        {
            "db": processing_session,
            "organization_id": organization_id,
            "work_kind": (
                vector_worker_module.WORK_KIND_ALERT_RECONCILIATION
            ),
        }
    ]

    assert discovery_session.closed is True

    assert processing_session.committed is True
    assert processing_session.rolled_back is False
    assert processing_session.closed is True

def test_stale_alert_reconciliation_candidate_is_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()

    candidate = SimpleNamespace(
        organization_id=organization_id,
    )

    discovery_session = FakeSession()
    processing_session = FakeSession()

    sessions = iter(
        [
            discovery_session,
            processing_session,
        ]
    )

    monkeypatch.setattr(
        vector_worker_module,
        "SessionLocal",
        lambda: next(sessions),
    )

    monkeypatch.setattr(
        vector_worker_module,
        "discover_pending_alert_reconciliation_sources",
        lambda db, *, limit: [
            candidate
        ],
    )

    monkeypatch.setattr(
        vector_worker_module,
        "discover_alert_reconciliation_source",
        lambda db, *, organization_id: None,
    )

    executed = []

    def unexpected_execution(
        db,
        *,
        source,
    ):
        executed.append(
            source
        )

        raise AssertionError(
            "Stale Alert reconciliation source must not execute."
        )

    monkeypatch.setattr(
        vector_worker_module,
        "execute_alert_generation",
        unexpected_execution,
    )

    worker = _worker()

    stats = (
        worker.process_alert_reconciliation_organizations()
    )

    assert stats.selected == 1
    assert stats.completed == 0
    assert stats.failed == 0
    assert stats.deferred == 1

    assert executed == []

    assert discovery_session.closed is True

    assert processing_session.committed is False
    assert processing_session.rolled_back is False
    assert processing_session.closed is True

def test_alert_reconciliation_worker_rolls_back_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()

    candidate = SimpleNamespace(
        organization_id=organization_id,
    )

    source = SimpleNamespace(
        organization_id=organization_id,
    )

    discovery_session = FakeSession()
    processing_session = FakeSession()
    retry_session = FakeSession()

    sessions = iter(
        [
            discovery_session,
            processing_session,
            retry_session,
        ]
    )

    monkeypatch.setattr(
        vector_worker_module,
        "SessionLocal",
        lambda: next(sessions),
    )

    monkeypatch.setattr(
        vector_worker_module,
        "discover_pending_alert_reconciliation_sources",
        lambda db, *, limit: [
            candidate
        ],
    )

    monkeypatch.setattr(
        vector_worker_module,
        "discover_alert_reconciliation_source",
        lambda db, *, organization_id: source,
    )

    def fail_alert_generation(
        db,
        *,
        source,
    ):
        raise RuntimeError(
            "forced Alert reconciliation failure"
        )

    monkeypatch.setattr(
        vector_worker_module,
        "execute_alert_generation",
        fail_alert_generation,
    )

    retry_source_calls: list[dict[str, object]] = []

    def fake_discover_retry_source(
        db,
        *,
        organization_id,
    ):
        retry_source_calls.append(
            {
                "db": db,
                "organization_id": organization_id,
            }
        )
        return source

    monkeypatch.setattr(
        vector_worker_module,
        "discover_alert_reconciliation_source",
        fake_discover_retry_source,
    )

    retry_schedule_calls: list[dict[str, object]] = []

    def fake_schedule_retry(
        db,
        *,
        organization_id,
        work_kind,
        retry_seconds,
    ):
        retry_schedule_calls.append(
            {
                "db": db,
                "organization_id": organization_id,
                "work_kind": work_kind,
                "retry_seconds": retry_seconds,
            }
        )

    monkeypatch.setattr(
        vector_worker_module,
        "schedule_organization_work_retry",
        fake_schedule_retry,
    )

    worker = _worker()

    stats = (
        worker.process_alert_reconciliation_organizations()
    )

    assert stats.selected == 1
    assert stats.completed == 0
    assert stats.failed == 1
    assert stats.deferred == 0

    assert discovery_session.closed is True

    assert processing_session.committed is False
    assert processing_session.rolled_back is True
    assert processing_session.closed is True
    assert retry_source_calls == [
        {
            "db": processing_session,
            "organization_id": organization_id,
        },
        {
            "db": retry_session,
            "organization_id": organization_id,
        },
    ]

    assert retry_schedule_calls == [
        {
            "db": retry_session,
            "organization_id": organization_id,
            "work_kind": (
                vector_worker_module.WORK_KIND_ALERT_RECONCILIATION
            ),
            "retry_seconds": (
                vector_worker_module.ORGANIZATION_WORK_FAILURE_RETRY_SECONDS
            ),
        }
    ]

    assert retry_session.committed is True
    assert retry_session.rolled_back is False
    assert retry_session.closed is True


def test_run_cycle_runs_alert_reconciliation_independently_from_intelligence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()

    worker.process_pattern_detection = False
    worker.process_anomaly_detection = False

    calls = []

    monkeypatch.setattr(
        worker,
        "process_risk_events",
        lambda: vector_worker_module.BatchStats(),
    )

    def unexpected_intelligence():
        raise AssertionError(
            "Transactional intelligence must remain disabled."
        )

    monkeypatch.setattr(
        worker,
        "process_intelligence_organizations",
        unexpected_intelligence,
    )

    reconciliation_stats = (
        vector_worker_module.BatchStats(
            selected=1,
            completed=1,
        )
    )

    def reconciliation_stage():
        calls.append(
            "alert_reconciliation"
        )

        return reconciliation_stats

    monkeypatch.setattr(
        worker,
        "process_alert_reconciliation_organizations",
        reconciliation_stage,
    )

    stats = worker.run_cycle()

    assert calls == [
        "alert_reconciliation"
    ]

    assert (
        stats.alert_reconciliation_organizations
        is reconciliation_stats
    )