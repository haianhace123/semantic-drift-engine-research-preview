from __future__ import annotations

from dataclasses import dataclass
import os
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy.orm import Session

from src.models.worker_operational_state import (
    WORKER_KIND_EMAIL,
    WORKER_KINDS,
    WorkerOperationalState,
)


WORKER_HEARTBEAT_STALE_AFTER = timedelta(
    seconds=30
)
WORKER_OPERATIONAL_CONCERN_WINDOW = timedelta(
    minutes=5
)

WorkerInstanceStatus = Literal[
    "healthy",
    "degraded",
    "stale",
]

WorkerKindStatus = Literal[
    "healthy",
    "degraded",
    "unavailable",
    "disabled",
]


@dataclass(frozen=True)
class WorkerInstanceOperationalView:
    worker_kind: str
    worker_instance_id: str
    status: WorkerInstanceStatus
    started_at: datetime
    last_heartbeat_at: datetime
    last_cycle_started_at: datetime | None
    last_cycle_completed_at: datetime | None
    last_success_at: datetime | None
    consecutive_failures: int
    last_failure_at: datetime | None
    last_error: str | None


@dataclass(frozen=True)
class WorkerKindOperationalView:
    worker_kind: str
    status: WorkerKindStatus
    expected: bool
    fresh_instance_count: int
    degraded_instance_count: int
    recent_stale_instance_count: int
    instances: tuple[
        WorkerInstanceOperationalView,
        ...,
    ]


@dataclass(frozen=True)
class WorkerOperationalOverview:
    observed_at: datetime
    heartbeat_stale_after_seconds: int
    concern_window_seconds: int
    workers: tuple[
        WorkerKindOperationalView,
        ...,
    ]


def _email_worker_expected() -> bool:
    """Read deployment intent; invalid values must not hide missing workers."""
    raw = os.getenv("SDE_EMAIL_WORKER_EXPECTED", "false").strip().lower()
    if raw not in {"true", "false"}:
        raise ValueError(
            "SDE_EMAIL_WORKER_EXPECTED must be 'true' or 'false'."
        )
    return raw == "true"


def _expected_worker_kinds() -> frozenset[str]:
    # Core workers cannot be disabled in the operational overview.
    kinds = WORKER_KINDS - {WORKER_KIND_EMAIL}
    if _email_worker_expected():
        return frozenset(kinds | {WORKER_KIND_EMAIL})
    return frozenset(kinds)


def _normalize_now(
    value: datetime | None,
) -> datetime:
    current_time = (
        value
        or datetime.now(
            timezone.utc
        )
    )

    if current_time.tzinfo is None:
        raise ValueError(
            "now must be timezone-aware."
        )

    return current_time


def _instance_status(
    state: WorkerOperationalState,
    *,
    now: datetime,
) -> WorkerInstanceStatus:
    if (
        now - state.last_heartbeat_at
        > WORKER_HEARTBEAT_STALE_AFTER
    ):
        return "stale"

    if state.consecutive_failures > 0:
        return "degraded"

    return "healthy"


def get_worker_operational_overview(
    db: Session,
    *,
    now: datetime | None = None,
) -> WorkerOperationalOverview:
    current_time = _normalize_now(
        now
    )
    expected_kinds = _expected_worker_kinds()
    concern_cutoff = (
        current_time
        - WORKER_OPERATIONAL_CONCERN_WINDOW
    )

    states = (
        db.query(
            WorkerOperationalState
        )
        .filter(
            WorkerOperationalState.last_heartbeat_at
            >= concern_cutoff
        )
        .order_by(
            WorkerOperationalState.worker_kind.asc(),
            WorkerOperationalState.last_heartbeat_at.desc(),
            WorkerOperationalState.worker_instance_id.asc(),
        )
        .all()
    )

    states_by_kind: dict[
        str,
        list[WorkerOperationalState],
    ] = {
        worker_kind: []
        for worker_kind in WORKER_KINDS
    }

    for state in states:
        if state.worker_kind in states_by_kind:
            states_by_kind[
                state.worker_kind
            ].append(
                state
            )

    workers: list[
        WorkerKindOperationalView
    ] = []

    for worker_kind in sorted(
        WORKER_KINDS
    ):
        instance_views: list[
            WorkerInstanceOperationalView
        ] = []

        fresh_instance_count = 0
        degraded_instance_count = 0
        recent_stale_instance_count = 0

        for state in states_by_kind[
            worker_kind
        ]:
            status = _instance_status(
                state,
                now=current_time,
            )

            if status == "stale":
                recent_stale_instance_count += 1
            else:
                fresh_instance_count += 1

                if status == "degraded":
                    degraded_instance_count += 1

            instance_views.append(
                WorkerInstanceOperationalView(
                    worker_kind=state.worker_kind,
                    worker_instance_id=(
                        state.worker_instance_id
                    ),
                    status=status,
                    started_at=state.started_at,
                    last_heartbeat_at=(
                        state.last_heartbeat_at
                    ),
                    last_cycle_started_at=(
                        state.last_cycle_started_at
                    ),
                    last_cycle_completed_at=(
                        state.last_cycle_completed_at
                    ),
                    last_success_at=(
                        state.last_success_at
                    ),
                    consecutive_failures=(
                        state.consecutive_failures
                    ),
                    last_failure_at=(
                        state.last_failure_at
                    ),
                    last_error=state.last_error,
                )
            )

        expected = worker_kind in expected_kinds
        if (
            not expected
            and fresh_instance_count == 0
            and recent_stale_instance_count == 0
        ):
            # Opted-out workers are not unavailable. Do not hide a recent
            # failure or stale heartbeat from a formerly running worker.
            kind_status: WorkerKindStatus = "disabled"
        elif fresh_instance_count == 0:
            kind_status = "unavailable"
        elif (
            degraded_instance_count > 0
            or recent_stale_instance_count > 0
        ):
            kind_status = "degraded"
        else:
            kind_status = "healthy"

        workers.append(
            WorkerKindOperationalView(
                worker_kind=worker_kind,
                status=kind_status,
                expected=expected,
                fresh_instance_count=(
                    fresh_instance_count
                ),
                degraded_instance_count=(
                    degraded_instance_count
                ),
                recent_stale_instance_count=(
                    recent_stale_instance_count
                ),
                instances=tuple(
                    instance_views
                ),
            )
        )

    return WorkerOperationalOverview(
        observed_at=current_time,
        heartbeat_stale_after_seconds=int(
            WORKER_HEARTBEAT_STALE_AFTER
            .total_seconds()
        ),
        concern_window_seconds=int(
            WORKER_OPERATIONAL_CONCERN_WINDOW
            .total_seconds()
        ),
        workers=tuple(
            workers
        ),
    )
