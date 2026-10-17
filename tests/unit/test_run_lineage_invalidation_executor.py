from __future__ import annotations

import signal

import pytest

from scripts import run_lineage_invalidation_executor
from unittest.mock import Mock

class FakeWorker:
    instances: list["FakeWorker"] = []

    def __init__(
        self,
        *,
        poll_seconds: float,
        operational_reporter=None,
    ) -> None:
        self.poll_seconds = poll_seconds
        self.operational_reporter = operational_reporter
        self.run_called = False
        self.stop_called = False

        self.__class__.instances.append(
            self
        )

    def run(self) -> None:
        self.run_called = True

    def stop(self) -> None:
        self.stop_called = True


@pytest.fixture(autouse=True)
def mock_operational_reporting(
    monkeypatch: pytest.MonkeyPatch,
):
    reporter = Mock()
    heartbeat = Mock()

    monkeypatch.setattr(
        run_lineage_invalidation_executor,
        "create_worker_instance_id",
        Mock(return_value="lineage-test-worker"),
    )
    monkeypatch.setattr(
        run_lineage_invalidation_executor,
        "WorkerOperationalReporter",
        Mock(return_value=reporter),
    )
    monkeypatch.setattr(
        run_lineage_invalidation_executor,
        "WorkerOperationalHeartbeat",
        Mock(return_value=heartbeat),
    )

    return reporter, heartbeat


def test_main_builds_and_runs_worker(
    monkeypatch: pytest.MonkeyPatch,
    mock_operational_reporting,
) -> None:
    FakeWorker.instances.clear()

    handlers: dict[
        signal.Signals,
        object,
    ] = {}

    def fake_signal(
        sig,
        handler,
    ):
        handlers[sig] = handler

    monkeypatch.setattr(
        run_lineage_invalidation_executor,
        "LineageInvalidationExecutionWorker",
        FakeWorker,
    )
    monkeypatch.setattr(
        run_lineage_invalidation_executor.signal,
        "signal",
        fake_signal,
    )
    monkeypatch.setattr(
        run_lineage_invalidation_executor.settings,
        "LINEAGE_INVALIDATION_EXECUTOR_POLL_SECONDS",
        7.5,
    )

    reporter, heartbeat = (
        mock_operational_reporting
    )

    run_lineage_invalidation_executor.main()

    assert len(
        FakeWorker.instances
    ) == 1

    worker = FakeWorker.instances[0]

    assert worker.poll_seconds == 7.5
    assert worker.run_called is True
    run_lineage_invalidation_executor.WorkerOperationalReporter.assert_called_once_with(
        worker_kind=(
            run_lineage_invalidation_executor
            .WORKER_KIND_LINEAGE_INVALIDATION
        ),
        worker_instance_id="lineage-test-worker",
    )
    reporter.started.assert_called_once_with()
    run_lineage_invalidation_executor.WorkerOperationalHeartbeat.assert_called_once_with(
        reporter.heartbeat,
        on_failure=worker.stop,
    )
    heartbeat.start.assert_called_once_with()
    heartbeat.raise_if_failed.assert_called_once_with()
    heartbeat.stop.assert_called_once_with()
    assert worker.operational_reporter is reporter
    assert signal.SIGINT in handlers
    assert signal.SIGTERM in handlers


def test_signal_handler_requests_worker_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeWorker.instances.clear()

    handlers: dict[
        signal.Signals,
        object,
    ] = {}

    def fake_signal(
        sig,
        handler,
    ):
        handlers[sig] = handler

    monkeypatch.setattr(
        run_lineage_invalidation_executor,
        "LineageInvalidationExecutionWorker",
        FakeWorker,
    )
    monkeypatch.setattr(
        run_lineage_invalidation_executor.signal,
        "signal",
        fake_signal,
    )

    run_lineage_invalidation_executor.main()

    worker = FakeWorker.instances[0]

    handler = handlers[
        signal.SIGTERM
    ]

    handler(
        signal.SIGTERM,
        None,
    )

    assert worker.stop_called is True


def test_main_does_not_swallow_worker_failure(
    monkeypatch: pytest.MonkeyPatch,
    mock_operational_reporting,
) -> None:
    class FailingWorker(
        FakeWorker
    ):
        def run(self) -> None:
            raise RuntimeError(
                "worker failed"
            )

    monkeypatch.setattr(
        run_lineage_invalidation_executor,
        "LineageInvalidationExecutionWorker",
        FailingWorker,
    )
    monkeypatch.setattr(
        run_lineage_invalidation_executor.signal,
        "signal",
        lambda sig, handler: None,
    )

    _, heartbeat = (
        mock_operational_reporting
    )

    with pytest.raises(
        RuntimeError,
        match="worker failed",
    ):
        run_lineage_invalidation_executor.main()

    heartbeat.stop.assert_called_once_with()
    heartbeat.raise_if_failed.assert_not_called()


def test_heartbeat_failure_stops_executor_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    mock_operational_reporting,
) -> None:
    FakeWorker.instances.clear()
    heartbeat = mock_operational_reporting[1]
    heartbeat.raise_if_failed.side_effect = RuntimeError(
        "worker operational heartbeat failed"
    )

    class ExecutorWaitingForHeartbeat(FakeWorker):
        def run(self) -> None:
            self.run_called = True
            callback = (
                run_lineage_invalidation_executor.WorkerOperationalHeartbeat
                .call_args.kwargs["on_failure"]
            )
            callback()
            assert self.stop_called

    monkeypatch.setattr(
        run_lineage_invalidation_executor,
        "LineageInvalidationExecutionWorker",
        ExecutorWaitingForHeartbeat,
    )
    monkeypatch.setattr(
        run_lineage_invalidation_executor.signal, "signal", lambda *args: None
    )

    with pytest.raises(
        RuntimeError,
        match="worker operational heartbeat failed",
    ):
        run_lineage_invalidation_executor.main()

    assert FakeWorker.instances[-1].stop_called
    heartbeat.stop.assert_called_once_with()