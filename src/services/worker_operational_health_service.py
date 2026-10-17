from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models.worker_operational_state import (
    WORKER_KINDS,
    WorkerOperationalState,
)


class WorkerOperationalHealthValidationError(ValueError):
    pass


def _normalize_worker_kind(value: str) -> str:
    if not isinstance(value, str):
        raise WorkerOperationalHealthValidationError(
            "worker_kind must be a string."
        )

    normalized = value.strip()

    if normalized not in WORKER_KINDS:
        raise WorkerOperationalHealthValidationError(
            f"Unsupported worker_kind: {normalized!r}."
        )

    return normalized


def _normalize_worker_instance_id(value: str) -> str:
    if not isinstance(value, str):
        raise WorkerOperationalHealthValidationError(
            "worker_instance_id must be a string."
        )

    normalized = value.strip()

    if not normalized:
        raise WorkerOperationalHealthValidationError(
            "worker_instance_id must not be empty."
        )

    if len(normalized) > 255:
        raise WorkerOperationalHealthValidationError(
            "worker_instance_id must not exceed 255 characters."
        )

    return normalized


def _normalize_now(value: datetime | None) -> datetime:
    current_time = value or datetime.now(timezone.utc)

    if current_time.tzinfo is None:
        raise WorkerOperationalHealthValidationError(
            "now must be timezone-aware."
        )

    return current_time


def _normalize_error(value: object) -> str:
    normalized = str(value).strip()

    if not normalized:
        return "Unknown worker cycle failure."

    return normalized


def register_worker_started(
    db: Session,
    *,
    worker_kind: str,
    worker_instance_id: str,
    now: datetime | None = None,
) -> datetime:
    """
    Register the start of one worker process incarnation.

    The caller owns commit/rollback.
    """

    normalized_kind = _normalize_worker_kind(
        worker_kind
    )
    normalized_instance_id = _normalize_worker_instance_id(
        worker_instance_id
    )
    current_time = _normalize_now(now)

    table = WorkerOperationalState.__table__

    statement = insert(table).values(
        worker_kind=normalized_kind,
        worker_instance_id=normalized_instance_id,
        started_at=current_time,
        last_heartbeat_at=current_time,
        consecutive_failures=0,
        updated_at=current_time,
    )

    statement = statement.on_conflict_do_update(
        index_elements=[
            table.c.worker_kind,
            table.c.worker_instance_id,
        ],
        set_={
            "started_at": current_time,
            "last_heartbeat_at": current_time,
            "last_cycle_started_at": None,
            "last_cycle_completed_at": None,
            "last_success_at": None,
            "consecutive_failures": 0,
            "last_failure_at": None,
            "last_error": None,
            "updated_at": current_time,
        },
    )

    db.execute(statement)

    return current_time


def record_worker_heartbeat(
    db: Session,
    *,
    worker_kind: str,
    worker_instance_id: str,
    now: datetime | None = None,
) -> datetime:
    """
    Record process liveness without changing cycle success or failure state.

    The caller owns commit/rollback.
    """

    normalized_kind = _normalize_worker_kind(
        worker_kind
    )
    normalized_instance_id = _normalize_worker_instance_id(
        worker_instance_id
    )
    current_time = _normalize_now(now)

    table = WorkerOperationalState.__table__

    statement = insert(table).values(
        worker_kind=normalized_kind,
        worker_instance_id=normalized_instance_id,
        started_at=current_time,
        last_heartbeat_at=current_time,
        consecutive_failures=0,
        updated_at=current_time,
    )

    statement = statement.on_conflict_do_update(
        index_elements=[
            table.c.worker_kind,
            table.c.worker_instance_id,
        ],
        set_={
            "last_heartbeat_at": current_time,
            "updated_at": current_time,
        },
    )

    db.execute(statement)

    return current_time


def record_worker_cycle_started(
    db: Session,
    *,
    worker_kind: str,
    worker_instance_id: str,
    now: datetime | None = None,
) -> datetime:
    """
    Record the beginning of a worker cycle.

    The caller owns commit/rollback.
    """

    normalized_kind = _normalize_worker_kind(
        worker_kind
    )
    normalized_instance_id = _normalize_worker_instance_id(
        worker_instance_id
    )
    current_time = _normalize_now(now)

    table = WorkerOperationalState.__table__

    statement = insert(table).values(
        worker_kind=normalized_kind,
        worker_instance_id=normalized_instance_id,
        started_at=current_time,
        last_heartbeat_at=current_time,
        last_cycle_started_at=current_time,
        consecutive_failures=0,
        updated_at=current_time,
    )

    statement = statement.on_conflict_do_update(
        index_elements=[
            table.c.worker_kind,
            table.c.worker_instance_id,
        ],
        set_={
            "last_heartbeat_at": current_time,
            "last_cycle_started_at": current_time,
            "updated_at": current_time,
        },
    )

    db.execute(statement)

    return current_time


def record_worker_cycle_succeeded(
    db: Session,
    *,
    worker_kind: str,
    worker_instance_id: str,
    now: datetime | None = None,
) -> datetime:
    """
    Record successful completion and clear consecutive failure state.

    The caller owns commit/rollback.
    """

    normalized_kind = _normalize_worker_kind(
        worker_kind
    )
    normalized_instance_id = _normalize_worker_instance_id(
        worker_instance_id
    )
    current_time = _normalize_now(now)

    table = WorkerOperationalState.__table__

    statement = insert(table).values(
        worker_kind=normalized_kind,
        worker_instance_id=normalized_instance_id,
        started_at=current_time,
        last_heartbeat_at=current_time,
        last_cycle_completed_at=current_time,
        last_success_at=current_time,
        consecutive_failures=0,
        updated_at=current_time,
    )

    statement = statement.on_conflict_do_update(
        index_elements=[
            table.c.worker_kind,
            table.c.worker_instance_id,
        ],
        set_={
            "last_heartbeat_at": current_time,
            "last_cycle_completed_at": current_time,
            "last_success_at": current_time,
            "consecutive_failures": 0,
            "last_error": None,
            "updated_at": current_time,
        },
    )

    db.execute(statement)

    return current_time


def record_worker_cycle_failed(
    db: Session,
    *,
    worker_kind: str,
    worker_instance_id: str,
    error: object,
    now: datetime | None = None,
) -> datetime:
    """
    Record failed completion and atomically increment consecutive failures.

    The caller owns commit/rollback.
    """

    normalized_kind = _normalize_worker_kind(
        worker_kind
    )
    normalized_instance_id = _normalize_worker_instance_id(
        worker_instance_id
    )
    current_time = _normalize_now(now)
    normalized_error = _normalize_error(error)

    table = WorkerOperationalState.__table__

    statement = insert(table).values(
        worker_kind=normalized_kind,
        worker_instance_id=normalized_instance_id,
        started_at=current_time,
        last_heartbeat_at=current_time,
        last_cycle_completed_at=current_time,
        consecutive_failures=1,
        last_failure_at=current_time,
        last_error=normalized_error,
        updated_at=current_time,
    )

    statement = statement.on_conflict_do_update(
        index_elements=[
            table.c.worker_kind,
            table.c.worker_instance_id,
        ],
        set_={
            "last_heartbeat_at": current_time,
            "last_cycle_completed_at": current_time,
            "consecutive_failures": (
                table.c.consecutive_failures + 1
            ),
            "last_failure_at": current_time,
            "last_error": normalized_error,
            "updated_at": current_time,
        },
    )

    db.execute(statement)

    return current_time
