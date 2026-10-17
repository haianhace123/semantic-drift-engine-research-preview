from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.models.worker_operational_state import (
    WORKER_KIND_EMAIL,
    WORKER_KIND_VECTOR,
    WORKER_KINDS,
)
from src.services.worker_operational_read_service import (
    get_worker_operational_overview,
)


NOW = datetime(
    2026,
    10,
    6,
    10,
    0,
    tzinfo=timezone.utc,
)


def _state(
    *,
    instance_id: str,
    heartbeat_age_seconds: int,
    failures: int = 0,
    worker_kind: str = WORKER_KIND_VECTOR,
):
    heartbeat = NOW - timedelta(
        seconds=heartbeat_age_seconds
    )

    return SimpleNamespace(
        worker_kind=worker_kind,
        worker_instance_id=instance_id,
        started_at=NOW - timedelta(
            minutes=10
        ),
        last_heartbeat_at=heartbeat,
        last_cycle_started_at=None,
        last_cycle_completed_at=None,
        last_success_at=None,
        consecutive_failures=failures,
        last_failure_at=None,
        last_error=(
            "cycle failed"
            if failures
            else None
        ),
    )


def _db_with_states(states):
    query = Mock()
    query.filter.return_value = query
    query.order_by.return_value = query
    # The production query applies the five-minute heartbeat concern filter.
    query.all.return_value = [
        state for state in states
        if state.last_heartbeat_at >= NOW - timedelta(minutes=5)
    ]

    db = Mock()
    db.query.return_value = query

    return db


def _vector(overview):
    return next(
        worker
        for worker in overview.workers
        if worker.worker_kind
        == WORKER_KIND_VECTOR
    )


def test_requires_timezone_aware_now() -> None:
    db = _db_with_states([])

    with pytest.raises(
        ValueError,
        match="now must be timezone-aware",
    ):
        get_worker_operational_overview(
            db,
            now=datetime(
                2026,
                10,
                6,
                10,
                0,
            ),
        )


def test_missing_worker_kind_is_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("SDE_EMAIL_WORKER_EXPECTED", raising=False)
    overview = get_worker_operational_overview(
        _db_with_states([]),
        now=NOW,
    )

    assert len(overview.workers) == len(WORKER_KINDS)
    assert all(
        worker.status == "unavailable" and worker.expected
        for worker in overview.workers
        if worker.worker_kind != WORKER_KIND_EMAIL
    )
    email = next(w for w in overview.workers if w.worker_kind == WORKER_KIND_EMAIL)
    assert email.status == "disabled"
    assert email.expected is False


def test_fresh_successful_instance_is_healthy() -> None:
    overview = get_worker_operational_overview(
        _db_with_states(
            [
                _state(
                    instance_id="vector-a",
                    heartbeat_age_seconds=10,
                )
            ]
        ),
        now=NOW,
    )

    vector = _vector(
        overview
    )

    assert vector.status == "healthy"
    assert vector.fresh_instance_count == 1
    assert vector.degraded_instance_count == 0
    assert vector.recent_stale_instance_count == 0
    assert vector.instances[0].status == "healthy"


def test_fresh_failed_instance_is_degraded() -> None:
    overview = get_worker_operational_overview(
        _db_with_states(
            [
                _state(
                    instance_id="vector-a",
                    heartbeat_age_seconds=10,
                    failures=2,
                )
            ]
        ),
        now=NOW,
    )

    vector = _vector(
        overview
    )

    assert vector.status == "degraded"
    assert vector.fresh_instance_count == 1
    assert vector.degraded_instance_count == 1
    assert vector.instances[0].status == "degraded"


def test_recent_stale_instance_is_unavailable_when_alone() -> None:
    overview = get_worker_operational_overview(
        _db_with_states(
            [
                _state(
                    instance_id="vector-a",
                    heartbeat_age_seconds=45,
                )
            ]
        ),
        now=NOW,
    )

    vector = _vector(
        overview
    )

    assert vector.status == "unavailable"
    assert vector.fresh_instance_count == 0
    assert vector.recent_stale_instance_count == 1
    assert vector.instances[0].status == "stale"


def test_recent_stale_instance_is_not_masked_by_fresh_instance() -> None:
    overview = get_worker_operational_overview(
        _db_with_states(
            [
                _state(
                    instance_id="vector-a",
                    heartbeat_age_seconds=10,
                ),
                _state(
                    instance_id="vector-b",
                    heartbeat_age_seconds=45,
                ),
            ]
        ),
        now=NOW,
    )

    vector = _vector(
        overview
    )

    assert vector.status == "degraded"
    assert vector.fresh_instance_count == 1
    assert vector.recent_stale_instance_count == 1


def test_overview_exposes_policy_windows() -> None:
    overview = get_worker_operational_overview(
        _db_with_states([]),
        now=NOW,
    )

    assert (
        overview.heartbeat_stale_after_seconds
        == 30
    )
    assert (
        overview.concern_window_seconds
        == 300
    )
