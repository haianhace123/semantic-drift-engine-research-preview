from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import uuid

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.models.worker_operational_state import (
    WORKER_KIND_VECTOR,
    WorkerOperationalState,
)
from src.services.worker_operational_health_service import (
    WorkerOperationalHealthValidationError,
    record_worker_cycle_failed,
    record_worker_cycle_started,
    record_worker_cycle_succeeded,
    record_worker_heartbeat,
    register_worker_started,
)
from src.services.worker_operational_read_service import (
    get_worker_operational_overview,
)


UTC = timezone.utc


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _instance_id() -> str:
    return f"test-vector-{uuid.uuid4().hex}"


def _vector_view(
    overview,
):
    return next(
        worker
        for worker in overview.workers
        if worker.worker_kind
        == WORKER_KIND_VECTOR
    )


def _load_state(
    db,
    *,
    worker_kind: str,
    worker_instance_id: str,
):
    db.expire_all()

    return (
        db.query(WorkerOperationalState)
        .filter(
            WorkerOperationalState.worker_kind
            == worker_kind,
            WorkerOperationalState.worker_instance_id
            == worker_instance_id,
        )
        .one_or_none()
    )


def _cleanup(
    worker_instance_ids: list[str],
) -> None:
    if not worker_instance_ids:
        return

    db = SessionLocal()

    try:
        db.rollback()

        db.execute(
            delete(
                WorkerOperationalState
            ).where(
                WorkerOperationalState.worker_instance_id.in_(
                    worker_instance_ids
                )
            )
        )

        db.commit()

    finally:
        db.close()


def test_register_worker_started_creates_and_resets_incarnation_state():
    db = SessionLocal()
    worker_instance_ids: list[str] = []

    try:
        worker_instance_id = _instance_id()
        worker_instance_ids.append(
            worker_instance_id
        )

        first_started_at = datetime(
            2036,
            10,
            1,
            3,
            0,
            tzinfo=UTC,
        )

        register_worker_started(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            now=first_started_at,
        )
        db.commit()

        record_worker_cycle_failed(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            error="temporary failure",
            now=datetime(
                2036,
                10,
                1,
                3,
                1,
                tzinfo=UTC,
            ),
        )
        db.commit()

        second_started_at = datetime(
            2036,
            10,
            1,
            4,
            0,
            tzinfo=UTC,
        )

        register_worker_started(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            now=second_started_at,
        )
        db.commit()

        state = _load_state(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
        )

        assert state is not None
        assert state.started_at == second_started_at
        assert state.last_heartbeat_at == second_started_at
        assert state.last_cycle_started_at is None
        assert state.last_cycle_completed_at is None
        assert state.last_success_at is None
        assert state.consecutive_failures == 0
        assert state.last_failure_at is None
        assert state.last_error is None

    finally:
        db.close()
        _cleanup(worker_instance_ids)


def test_heartbeat_preserves_cycle_failure_state():
    db = SessionLocal()
    worker_instance_ids: list[str] = []

    try:
        worker_instance_id = _instance_id()
        worker_instance_ids.append(
            worker_instance_id
        )

        started_at = datetime(
            2036,
            10,
            2,
            3,
            0,
            tzinfo=UTC,
        )
        failure_at = datetime(
            2036,
            10,
            2,
            3,
            1,
            tzinfo=UTC,
        )
        heartbeat_at = datetime(
            2036,
            10,
            2,
            3,
            2,
            tzinfo=UTC,
        )

        register_worker_started(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            now=started_at,
        )
        record_worker_cycle_failed(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            error="provider unavailable",
            now=failure_at,
        )
        db.commit()

        record_worker_heartbeat(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            now=heartbeat_at,
        )
        db.commit()

        state = _load_state(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
        )

        assert state is not None
        assert state.last_heartbeat_at == heartbeat_at
        assert state.consecutive_failures == 1
        assert state.last_failure_at == failure_at
        assert state.last_error == "provider unavailable"
        assert state.last_success_at is None

    finally:
        db.close()
        _cleanup(worker_instance_ids)


def test_cycle_started_updates_progress_without_clearing_failure():
    db = SessionLocal()
    worker_instance_ids: list[str] = []

    try:
        worker_instance_id = _instance_id()
        worker_instance_ids.append(
            worker_instance_id
        )

        register_worker_started(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            now=datetime(
                2036,
                10,
                3,
                3,
                0,
                tzinfo=UTC,
            ),
        )

        record_worker_cycle_failed(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            error="cycle failed",
            now=datetime(
                2036,
                10,
                3,
                3,
                1,
                tzinfo=UTC,
            ),
        )
        db.commit()

        cycle_started_at = datetime(
            2036,
            10,
            3,
            3,
            2,
            tzinfo=UTC,
        )

        record_worker_cycle_started(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            now=cycle_started_at,
        )
        db.commit()

        state = _load_state(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
        )

        assert state is not None
        assert state.last_cycle_started_at == cycle_started_at
        assert state.last_heartbeat_at == cycle_started_at
        assert state.consecutive_failures == 1
        assert state.last_error == "cycle failed"

    finally:
        db.close()
        _cleanup(worker_instance_ids)


def test_cycle_success_resets_consecutive_failure_state():
    db = SessionLocal()
    worker_instance_ids: list[str] = []

    try:
        worker_instance_id = _instance_id()
        worker_instance_ids.append(
            worker_instance_id
        )

        register_worker_started(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            now=datetime(
                2036,
                10,
                4,
                3,
                0,
                tzinfo=UTC,
            ),
        )

        record_worker_cycle_failed(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            error="first failure",
            now=datetime(
                2036,
                10,
                4,
                3,
                1,
                tzinfo=UTC,
            ),
        )

        record_worker_cycle_failed(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            error="second failure",
            now=datetime(
                2036,
                10,
                4,
                3,
                2,
                tzinfo=UTC,
            ),
        )
        db.commit()

        success_at = datetime(
            2036,
            10,
            4,
            3,
            3,
            tzinfo=UTC,
        )

        record_worker_cycle_succeeded(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            now=success_at,
        )
        db.commit()

        state = _load_state(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
        )

        assert state is not None
        assert state.last_cycle_completed_at == success_at
        assert state.last_success_at == success_at
        assert state.last_heartbeat_at == success_at
        assert state.consecutive_failures == 0
        assert state.last_error is None

    finally:
        db.close()
        _cleanup(worker_instance_ids)


def test_cycle_failure_increments_consecutive_failures():
    db = SessionLocal()
    worker_instance_ids: list[str] = []

    try:
        worker_instance_id = _instance_id()
        worker_instance_ids.append(
            worker_instance_id
        )

        register_worker_started(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            now=datetime(
                2036,
                10,
                5,
                3,
                0,
                tzinfo=UTC,
            ),
        )
        db.commit()

        first_failure_at = datetime(
            2036,
            10,
            5,
            3,
            1,
            tzinfo=UTC,
        )

        record_worker_cycle_failed(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            error="first failure",
            now=first_failure_at,
        )
        db.commit()

        second_failure_at = datetime(
            2036,
            10,
            5,
            3,
            2,
            tzinfo=UTC,
        )

        record_worker_cycle_failed(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            error=RuntimeError("second failure"),
            now=second_failure_at,
        )
        db.commit()

        state = _load_state(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
        )

        assert state is not None
        assert state.consecutive_failures == 2
        assert state.last_failure_at == second_failure_at
        assert state.last_cycle_completed_at == second_failure_at
        assert state.last_heartbeat_at == second_failure_at
        assert state.last_error == "second failure"

    finally:
        db.close()
        _cleanup(worker_instance_ids)


def test_worker_state_changes_roll_back_with_caller_transaction():
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    worker_instance_ids: list[str] = []

    try:
        worker_instance_id = _instance_id()
        worker_instance_ids.append(
            worker_instance_id
        )

        register_worker_started(
            execution_db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            now=datetime(
                2036,
                10,
                6,
                3,
                0,
                tzinfo=UTC,
            ),
        )

        execution_db.rollback()

        persisted = _load_state(
            verify_db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
        )

        assert persisted is None

    finally:
        execution_db.close()
        verify_db.close()
        _cleanup(worker_instance_ids)


@pytest.mark.parametrize(
    ("worker_kind", "worker_instance_id"),
    [
        ("unsupported", "worker-1"),
        (WORKER_KIND_VECTOR, ""),
        (WORKER_KIND_VECTOR, "   "),
        (WORKER_KIND_VECTOR, "x" * 256),
    ],
)
def test_worker_identity_validation_rejects_invalid_values(
    worker_kind,
    worker_instance_id,
):
    db = SessionLocal()

    try:
        with pytest.raises(
            WorkerOperationalHealthValidationError
        ):
            register_worker_started(
                db,
                worker_kind=worker_kind,
                worker_instance_id=worker_instance_id,
                now=datetime(
                    2036,
                    10,
                    7,
                    3,
                    0,
                    tzinfo=UTC,
                ),
            )

    finally:
        db.close()


def test_worker_timestamp_must_be_timezone_aware():
    db = SessionLocal()

    try:
        with pytest.raises(
            WorkerOperationalHealthValidationError,
            match="timezone-aware",
        ):
            register_worker_started(
                db,
                worker_kind=WORKER_KIND_VECTOR,
                worker_instance_id=_instance_id(),
                now=datetime(
                    2036,
                    10,
                    7,
                    3,
                    0,
                ),
            )

    finally:
        db.close()


def test_operational_read_keeps_recent_stale_instance_visible():
    db = SessionLocal()
    worker_instance_ids: list[str] = []

    try:
        worker_instance_id = _instance_id()
        worker_instance_ids.append(
            worker_instance_id
        )

        observed_at = datetime(
            2036,
            10,
            8,
            3,
            0,
            tzinfo=UTC,
        )

        register_worker_started(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            now=(
                observed_at
                - timedelta(
                    seconds=45
                )
            ),
        )
        db.commit()

        overview = get_worker_operational_overview(
            db,
            now=observed_at,
        )

        vector = _vector_view(
            overview
        )

        assert vector.status == "unavailable"
        assert vector.fresh_instance_count == 0
        assert vector.recent_stale_instance_count == 1
        assert len(
            vector.instances
        ) == 1
        assert (
            vector.instances[0].worker_instance_id
            == worker_instance_id
        )
        assert vector.instances[0].status == "stale"

    finally:
        db.close()
        _cleanup(worker_instance_ids)


def test_operational_read_excludes_historical_stale_instance():
    db = SessionLocal()
    worker_instance_ids: list[str] = []

    try:
        worker_instance_id = _instance_id()
        worker_instance_ids.append(
            worker_instance_id
        )

        observed_at = datetime(
            2036,
            10,
            8,
            4,
            0,
            tzinfo=UTC,
        )

        register_worker_started(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
            now=(
                observed_at
                - timedelta(
                    minutes=6
                )
            ),
        )
        db.commit()

        overview = get_worker_operational_overview(
            db,
            now=observed_at,
        )

        vector = _vector_view(
            overview
        )

        assert vector.status == "unavailable"
        assert vector.fresh_instance_count == 0
        assert vector.degraded_instance_count == 0
        assert vector.recent_stale_instance_count == 0
        assert vector.instances == ()

        persisted = _load_state(
            db,
            worker_kind=WORKER_KIND_VECTOR,
            worker_instance_id=worker_instance_id,
        )

        assert persisted is not None

    finally:
        db.close()
        _cleanup(worker_instance_ids)
