from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.services import worker_operational_reporter
from src.services.worker_operational_reporter import (
    WorkerOperationalReporter,
)


def _make_reporter():
    db = Mock()
    session_factory = Mock(return_value=db)

    reporter = WorkerOperationalReporter(
        worker_kind="vector",
        worker_instance_id="worker-1",
        session_factory=session_factory,
    )

    return reporter, db, session_factory


@pytest.mark.parametrize(
    ("method_name", "service_name"),
    [
        ("started", "register_worker_started"),
        ("heartbeat", "record_worker_heartbeat"),
        ("cycle_started", "record_worker_cycle_started"),
        ("cycle_succeeded", "record_worker_cycle_succeeded"),
    ],
)
def test_reporter_commits_successful_write(
    monkeypatch,
    method_name,
    service_name,
):
    reporter, db, session_factory = _make_reporter()

    operation = Mock()
    monkeypatch.setattr(
        worker_operational_reporter,
        service_name,
        operation,
    )

    getattr(reporter, method_name)()

    session_factory.assert_called_once_with()
    operation.assert_called_once_with(
        db,
        worker_kind="vector",
        worker_instance_id="worker-1",
        now=None,
    )
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()
    db.close.assert_called_once_with()


def test_cycle_failed_passes_error_and_commits(
    monkeypatch,
):
    reporter, db, _ = _make_reporter()
    operation = Mock()

    monkeypatch.setattr(
        worker_operational_reporter,
        "record_worker_cycle_failed",
        operation,
    )

    error = RuntimeError("boom")

    reporter.cycle_failed(error)

    operation.assert_called_once_with(
        db,
        worker_kind="vector",
        worker_instance_id="worker-1",
        error=error,
        now=None,
    )
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()
    db.close.assert_called_once_with()


def test_reporter_rolls_back_and_closes_on_failure(
    monkeypatch,
):
    reporter, db, _ = _make_reporter()

    operation = Mock(
        side_effect=RuntimeError("database unavailable")
    )

    monkeypatch.setattr(
        worker_operational_reporter,
        "record_worker_heartbeat",
        operation,
    )

    with pytest.raises(
        RuntimeError,
        match="database unavailable",
    ):
        reporter.heartbeat()

    db.commit.assert_not_called()
    db.rollback.assert_called_once_with()
    db.close.assert_called_once_with()
