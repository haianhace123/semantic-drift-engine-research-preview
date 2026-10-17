from __future__ import annotations

import signal
from unittest.mock import Mock

import pytest

from scripts import run_crawler


class FakeScheduler:
    instances: list["FakeScheduler"] = []

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
        run_crawler,
        "create_worker_instance_id",
        Mock(return_value="crawler-test-worker"),
    )
    monkeypatch.setattr(
        run_crawler,
        "WorkerOperationalReporter",
        Mock(return_value=reporter),
    )
    monkeypatch.setattr(
        run_crawler,
        "WorkerOperationalHeartbeat",
        Mock(return_value=heartbeat),
    )

    return reporter, heartbeat


def test_main_builds_and_runs_scheduler(
    monkeypatch: pytest.MonkeyPatch,
    mock_operational_reporting,
) -> None:
    FakeScheduler.instances.clear()

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
        run_crawler,
        "CrawlScheduler",
        FakeScheduler,
    )

    monkeypatch.setattr(
        run_crawler.signal,
        "signal",
        fake_signal,
    )

    monkeypatch.setattr(
        run_crawler.settings,
        "CRAWLER_SCHEDULER_POLL_SECONDS",
        7.5,
    )

    run_crawler.main()

    assert len(
        FakeScheduler.instances
    ) == 1

    worker = FakeScheduler.instances[0]
    reporter, heartbeat = (
        mock_operational_reporting
    )

    assert worker.poll_seconds == 7.5
    assert worker.run_called is True

    run_crawler.WorkerOperationalReporter.assert_called_once_with(
        worker_kind=run_crawler.WORKER_KIND_CRAWLER,
        worker_instance_id="crawler-test-worker",
    )
    reporter.started.assert_called_once_with()
    run_crawler.WorkerOperationalHeartbeat.assert_called_once_with(
        reporter.heartbeat,
        on_failure=worker.stop,
    )
    heartbeat.start.assert_called_once_with()
    heartbeat.raise_if_failed.assert_called_once_with()
    heartbeat.stop.assert_called_once_with()
    assert worker.operational_reporter is reporter


    assert signal.SIGINT in handlers
    assert signal.SIGTERM in handlers


def test_signal_handler_requests_scheduler_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeScheduler.instances.clear()

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
        run_crawler,
        "CrawlScheduler",
        FakeScheduler,
    )

    monkeypatch.setattr(
        run_crawler.signal,
        "signal",
        fake_signal,
    )

    run_crawler.main()

    worker = FakeScheduler.instances[0]

    handler = handlers[
        signal.SIGTERM
    ]

    handler(
        signal.SIGTERM,
        None,
    )

    assert worker.stop_called is True


def test_main_does_not_swallow_scheduler_failure(
    monkeypatch: pytest.MonkeyPatch,
    mock_operational_reporting,
) -> None:
    class FailingScheduler(
        FakeScheduler
    ):
        def run(self) -> None:
            raise RuntimeError(
                "scheduler failed"
            )

    monkeypatch.setattr(
        run_crawler,
        "CrawlScheduler",
        FailingScheduler,
    )

    monkeypatch.setattr(
        run_crawler.signal,
        "signal",
        lambda sig, handler: None,
    )

    _, heartbeat = (
        mock_operational_reporting
    )

    with pytest.raises(
        RuntimeError,
        match="scheduler failed",
    ):
        run_crawler.main()

    heartbeat.stop.assert_called_once_with()
    heartbeat.raise_if_failed.assert_not_called()


def test_heartbeat_failure_stops_scheduler_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    mock_operational_reporting,
) -> None:
    FakeScheduler.instances.clear()
    heartbeat = mock_operational_reporting[1]
    heartbeat.raise_if_failed.side_effect = RuntimeError(
        "worker operational heartbeat failed"
    )

    class SchedulerWaitingForHeartbeat(FakeScheduler):
        def run(self) -> None:
            self.run_called = True
            callback = run_crawler.WorkerOperationalHeartbeat.call_args.kwargs[
                "on_failure"
            ]
            callback()
            assert self.stop_called

    monkeypatch.setattr(
        run_crawler, "CrawlScheduler", SchedulerWaitingForHeartbeat
    )
    monkeypatch.setattr(run_crawler.signal, "signal", lambda *args: None)

    with pytest.raises(
        RuntimeError,
        match="worker operational heartbeat failed",
    ):
        run_crawler.main()

    assert FakeScheduler.instances[-1].stop_called
    heartbeat.stop.assert_called_once_with()
