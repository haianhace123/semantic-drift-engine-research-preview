from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from src.services.worker_operational_read_service import (
    WorkerOperationalOverview,
)


class WorkerOperationalInstanceResponse(BaseModel):
    worker_instance_id: str
    status: Literal[
        "healthy",
        "degraded",
        "stale",
    ]
    started_at: datetime
    last_heartbeat_at: datetime
    last_cycle_started_at: datetime | None
    last_cycle_completed_at: datetime | None
    last_success_at: datetime | None
    consecutive_failures: int
    last_failure_at: datetime | None
    last_error: str | None


class WorkerOperationalKindResponse(BaseModel):
    worker_kind: str
    status: Literal[
        "healthy",
        "degraded",
        "unavailable",
        "disabled",
    ]
    expected: bool
    fresh_instance_count: int
    degraded_instance_count: int
    recent_stale_instance_count: int
    instances: list[
        WorkerOperationalInstanceResponse
    ]


class WorkerOperationsResponse(BaseModel):
    observed_at: datetime
    heartbeat_stale_after_seconds: int
    concern_window_seconds: int
    workers: list[
        WorkerOperationalKindResponse
    ]


def worker_operations_response(
    overview: WorkerOperationalOverview,
) -> WorkerOperationsResponse:
    return WorkerOperationsResponse(
        observed_at=overview.observed_at,
        heartbeat_stale_after_seconds=(
            overview.heartbeat_stale_after_seconds
        ),
        concern_window_seconds=(
            overview.concern_window_seconds
        ),
        workers=[
            WorkerOperationalKindResponse(
                worker_kind=worker.worker_kind,
                status=worker.status,
                expected=worker.expected,
                fresh_instance_count=(
                    worker.fresh_instance_count
                ),
                degraded_instance_count=(
                    worker.degraded_instance_count
                ),
                recent_stale_instance_count=(
                    worker.recent_stale_instance_count
                ),
                # Worker identities and raw failure exceptions are
                # deployment-global internals, not tenant-owned data.
                # Preserve the existing response shape without disclosing
                # individual worker records to organization admins.
                instances=[],
            )
            for worker in overview.workers
        ],
    )
