from __future__ import annotations

import signal
import sys
from unittest.mock import Mock

import pytest

import scripts.vector_worker as worker_module


def _configure_main(
    monkeypatch,
    *,
    once: bool,
):
    worker = Mock()
    reporter = Mock()
    heartbeat = Mock()

    monkeypatch.setattr(
        worker_module,
        "VectorWorker",
        Mock(return_value=worker),
    )
    monkeypatch.setattr(
        worker_module,
        "create_worker_instance_id",
        Mock(return_value="vector-test-worker"),
    )
    monkeypatch.setattr(
        worker_module,
        "WorkerOperationalReporter",
        Mock(return_value=reporter),
    )
    monkeypatch.setattr(
        worker_module,
        "WorkerOperationalHeartbeat",
        Mock(return_value=heartbeat),
    )
    monkeypatch.setattr(
        worker_module.signal,
        "signal",
        Mock(),
    )

    argv = ["vector_worker"]

    if once:
        argv.append("--once")

    monkeypatch.setattr(
        sys,
        "argv",
        argv,
    )

    return worker, reporter, heartbeat


def test_once_records_successful_cycle(
    monkeypatch,
):
    worker, reporter, heartbeat = _configure_main(
        monkeypatch,
        once=True,
    )

    result = worker_module.main()

    assert result == 0

    worker_module.WorkerOperationalReporter.assert_called_once_with(
        worker_kind=worker_module.WORKER_KIND_VECTOR,
        worker_instance_id="vector-test-worker",
    )
    reporter.started.assert_called_once_with()
    worker_module.WorkerOperationalHeartbeat.assert_called_once_with(
        reporter.heartbeat,
    )
    heartbeat.start.assert_called_once_with()
    reporter.cycle_started.assert_called_once_with()
    worker.run_cycle.assert_called_once_with()
    reporter.cycle_succeeded.assert_called_once_with()
    reporter.cycle_failed.assert_not_called()
    heartbeat.raise_if_failed.assert_called_once_with()
    heartbeat.stop.assert_called_once_with()


def test_once_records_failure_and_preserves_exception(
    monkeypatch,
):
    worker, reporter, heartbeat = _configure_main(
        monkeypatch,
        once=True,
    )

    error = RuntimeError("cycle failed")
    worker.run_cycle.side_effect = error

    with pytest.raises(
        RuntimeError,
        match="cycle failed",
    ):
        worker_module.main()

    reporter.started.assert_called_once_with()
    reporter.cycle_started.assert_called_once_with()
    reporter.cycle_failed.assert_called_once_with(
        error
    )
    reporter.cycle_succeeded.assert_not_called()
    heartbeat.raise_if_failed.assert_not_called()
    heartbeat.stop.assert_called_once_with()


def test_continuous_cycle_records_success_before_shutdown(
    monkeypatch,
):
    worker, reporter, heartbeat = _configure_main(
        monkeypatch,
        once=False,
    )

    registered_handlers = {}

    def register_signal(
        sig,
        handler,
    ):
        registered_handlers[sig] = handler

    worker_module.signal.signal.side_effect = (
        register_signal
    )

    def run_cycle():
        registered_handlers[signal.SIGINT](
            signal.SIGINT,
            None,
        )

    worker.run_cycle.side_effect = run_cycle

    result = worker_module.main()

    assert result == 0
    reporter.started.assert_called_once_with()
    reporter.cycle_started.assert_called_once_with()
    worker.run_cycle.assert_called_once_with()
    reporter.cycle_succeeded.assert_called_once_with()
    reporter.cycle_failed.assert_not_called()
    assert heartbeat.raise_if_failed.call_count == 2
    heartbeat.stop.assert_called_once_with()


def test_continuous_cycle_records_failure_and_keeps_loop_policy(
    monkeypatch,
):
    worker, reporter, heartbeat = _configure_main(
        monkeypatch,
        once=False,
    )

    registered_handlers = {}

    def register_signal(
        sig,
        handler,
    ):
        registered_handlers[sig] = handler

    worker_module.signal.signal.side_effect = (
        register_signal
    )

    error = RuntimeError("cycle failed")

    def run_cycle():
        registered_handlers[signal.SIGINT](
            signal.SIGINT,
            None,
        )
        raise error

    worker.run_cycle.side_effect = run_cycle

    result = worker_module.main()

    assert result == 0
    reporter.started.assert_called_once_with()
    reporter.cycle_started.assert_called_once_with()
    reporter.cycle_failed.assert_called_once_with(
        error
    )
    reporter.cycle_succeeded.assert_not_called()
    assert heartbeat.raise_if_failed.call_count == 2
    heartbeat.stop.assert_called_once_with()
