from __future__ import annotations

import math

import pytest

from unittest.mock import Mock

from src.services import (
    lineage_invalidation_execution_worker as worker_module,
)
from src.services.lineage_invalidation_execution_cycle_service import (
    LineageInvalidationExecutionCycleResult,
)


@pytest.mark.parametrize(
    "poll_seconds",
    [
        0,
        -1,
        math.nan,
        math.inf,
        -math.inf,
    ],
)
def test_worker_rejects_invalid_poll_seconds(
    poll_seconds: float,
) -> None:
    with pytest.raises(
        worker_module.LineageInvalidationExecutionWorkerConfigurationError,
        match="finite and greater than zero",
    ):
        worker_module.LineageInvalidationExecutionWorker(
            poll_seconds=poll_seconds,
        )


@pytest.mark.parametrize(
    "status",
    [
        worker_module.LineageInvalidationExecutionCycleStatus.IDLE,
        worker_module.LineageInvalidationExecutionCycleStatus.SKIPPED,
    ],
)
def test_worker_waits_after_non_progress_cycle(
    monkeypatch: pytest.MonkeyPatch,
    status: worker_module.LineageInvalidationExecutionCycleStatus,
) -> None:
    worker = worker_module.LineageInvalidationExecutionWorker(
        poll_seconds=5,
    )

    calls = 0

    def fake_cycle():
        nonlocal calls

        calls += 1

        if calls == 1:
            return LineageInvalidationExecutionCycleResult(
                status=status,
            )

        worker.stop()

        return LineageInvalidationExecutionCycleResult(
            status=worker_module.LineageInvalidationExecutionCycleStatus.IDLE,
        )

    waits: list[float] = []

    def fake_wait(
        timeout: float,
    ) -> bool:
        waits.append(
            timeout
        )

        return False

    monkeypatch.setattr(
        worker_module,
        "run_lineage_invalidation_execution_cycle",
        fake_cycle,
    )
    monkeypatch.setattr(
        worker.stop_event,
        "wait",
        fake_wait,
    )

    worker.run()

    assert calls == 2
    assert waits == [
        5,
    ]


@pytest.mark.parametrize(
    "status",
    [
        worker_module.LineageInvalidationExecutionCycleStatus.SUCCEEDED,
        worker_module.LineageInvalidationExecutionCycleStatus.FAILED,
    ],
)
def test_worker_drains_after_completed_work(
    monkeypatch: pytest.MonkeyPatch,
    status: worker_module.LineageInvalidationExecutionCycleStatus,
) -> None:
    worker = worker_module.LineageInvalidationExecutionWorker(
        poll_seconds=5,
    )

    calls = 0

    def fake_cycle():
        nonlocal calls

        calls += 1

        if calls == 1:
            return LineageInvalidationExecutionCycleResult(
                status=status,
            )

        worker.stop()

        return LineageInvalidationExecutionCycleResult(
            status=worker_module.LineageInvalidationExecutionCycleStatus.IDLE,
        )

    waits: list[float] = []

    def fake_wait(
        timeout: float,
    ) -> bool:
        waits.append(
            timeout
        )

        return False

    monkeypatch.setattr(
        worker_module,
        "run_lineage_invalidation_execution_cycle",
        fake_cycle,
    )
    monkeypatch.setattr(
        worker.stop_event,
        "wait",
        fake_wait,
    )

    worker.run()

    assert calls == 2
    assert waits == []


def test_worker_recovers_after_unexpected_cycle_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = worker_module.LineageInvalidationExecutionWorker(
        poll_seconds=5,
    )

    calls = 0

    def fake_cycle():
        nonlocal calls

        calls += 1

        if calls == 1:
            raise RuntimeError(
                "transient cycle failure"
            )

        worker.stop()

        return LineageInvalidationExecutionCycleResult(
            status=worker_module.LineageInvalidationExecutionCycleStatus.IDLE,
        )

    waits: list[float] = []

    def fake_wait(
        timeout: float,
    ) -> bool:
        waits.append(
            timeout
        )

        return False

    monkeypatch.setattr(
        worker_module,
        "run_lineage_invalidation_execution_cycle",
        fake_cycle,
    )
    monkeypatch.setattr(
        worker.stop_event,
        "wait",
        fake_wait,
    )

    worker.run()

    assert calls == 2
    assert waits == [
        5,
    ]


def test_worker_stop_interrupts_poll_wait() -> None:
    worker = worker_module.LineageInvalidationExecutionWorker(
        poll_seconds=60,
    )

    worker.stop()

    assert worker.stop_event.is_set()


def test_worker_stop_during_active_cycle_prevents_next_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = worker_module.LineageInvalidationExecutionWorker(
        poll_seconds=5,
    )

    calls = 0

    def fake_cycle():
        nonlocal calls

        calls += 1
        worker.stop()

        return LineageInvalidationExecutionCycleResult(
            status=worker_module.LineageInvalidationExecutionCycleStatus.SUCCEEDED,
        )

    monkeypatch.setattr(
        worker_module,
        "run_lineage_invalidation_execution_cycle",
        fake_cycle,
    )

    worker.run()

    assert calls == 1


def test_worker_logs_success_without_idle_noise(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = worker_module.LineageInvalidationExecutionWorker(
        poll_seconds=5,
    )
    calls = 0

    def fake_cycle():
        nonlocal calls

        calls += 1

        if calls == 1:
            return LineageInvalidationExecutionCycleResult(
                status=(
                    worker_module
                    .LineageInvalidationExecutionCycleStatus
                    .SUCCEEDED
                ),
            )

        worker.stop()

        return LineageInvalidationExecutionCycleResult(
            status=(
                worker_module
                .LineageInvalidationExecutionCycleStatus
                .IDLE
            ),
        )

    monkeypatch.setattr(
        worker_module,
        "run_lineage_invalidation_execution_cycle",
        fake_cycle,
    )

    with caplog.at_level(
        "INFO",
        logger=worker_module.__name__,
    ):
        worker.run()

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == worker_module.__name__
    ]

    assert len(messages) == 1
    assert "status=succeeded" in messages[0]
    assert "duration_ms=" in messages[0]


def test_worker_logs_skipped_cycle_as_info(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = worker_module.LineageInvalidationExecutionWorker(
        poll_seconds=5,
    )

    def fake_cycle():
        worker.stop()

        return LineageInvalidationExecutionCycleResult(
            status=(
                worker_module
                .LineageInvalidationExecutionCycleStatus
                .SKIPPED
            ),
            reason_code="target_binding_not_found",
        )

    monkeypatch.setattr(
        worker_module,
        "run_lineage_invalidation_execution_cycle",
        fake_cycle,
    )

    with caplog.at_level(
        "INFO",
        logger=worker_module.__name__,
    ):
        worker.run()

    records = [
        record
        for record in caplog.records
        if record.name == worker_module.__name__
    ]

    assert len(records) == 1
    assert records[0].levelname == "INFO"
    assert "status=skipped" in records[0].getMessage()
    assert (
        "reason_code=target_binding_not_found"
        in records[0].getMessage()
    )


def test_worker_logs_failed_cycle_as_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = worker_module.LineageInvalidationExecutionWorker(
        poll_seconds=5,
    )

    def fake_cycle():
        worker.stop()

        return LineageInvalidationExecutionCycleResult(
            status=(
                worker_module
                .LineageInvalidationExecutionCycleStatus
                .FAILED
            ),
            reason_code="downstream_execution_failed",
        )

    monkeypatch.setattr(
        worker_module,
        "run_lineage_invalidation_execution_cycle",
        fake_cycle,
    )

    with caplog.at_level(
        "ERROR",
        logger=worker_module.__name__,
    ):
        worker.run()

    records = [
        record
        for record in caplog.records
        if record.name == worker_module.__name__
    ]

    assert len(records) == 1
    assert records[0].levelname == "ERROR"
    assert "status=failed" in records[0].getMessage()
    assert (
        "reason_code=downstream_execution_failed"
        in records[0].getMessage()
    )


def test_worker_logs_unexpected_cycle_error_with_duration(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = worker_module.LineageInvalidationExecutionWorker(
        poll_seconds=5,
    )
    calls = 0

    def fake_cycle():
        nonlocal calls

        calls += 1

        if calls == 1:
            raise RuntimeError(
                "transient cycle failure"
            )

        worker.stop()

        return LineageInvalidationExecutionCycleResult(
            status=(
                worker_module
                .LineageInvalidationExecutionCycleStatus
                .IDLE
            ),
        )

    monkeypatch.setattr(
        worker_module,
        "run_lineage_invalidation_execution_cycle",
        fake_cycle,
    )
    monkeypatch.setattr(
        worker.stop_event,
        "wait",
        lambda timeout: False,
    )

    with caplog.at_level(
        "ERROR",
        logger=worker_module.__name__,
    ):
        worker.run()

    records = [
        record
        for record in caplog.records
        if record.name == worker_module.__name__
    ]

    assert len(records) == 1
    assert records[0].levelname == "ERROR"
    assert "duration_ms=" in records[0].getMessage()
    assert records[0].exc_info is not None


def test_worker_reports_failed_result_as_operational_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter = Mock()
    worker = worker_module.LineageInvalidationExecutionWorker(
        poll_seconds=5,
        operational_reporter=reporter,
    )

    def fake_cycle():
        worker.stop()

        return LineageInvalidationExecutionCycleResult(
            status=(
                worker_module
                .LineageInvalidationExecutionCycleStatus
                .FAILED
            ),
            reason_code="downstream_execution_failed",
        )

    monkeypatch.setattr(
        worker_module,
        "run_lineage_invalidation_execution_cycle",
        fake_cycle,
    )

    worker.run()

    reporter.cycle_started.assert_called_once_with()
    reporter.cycle_succeeded.assert_called_once_with()
    reporter.cycle_failed.assert_not_called()


def test_worker_reports_unexpected_failure_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter = Mock()
    worker = worker_module.LineageInvalidationExecutionWorker(
        poll_seconds=5,
        operational_reporter=reporter,
    )

    error = RuntimeError("transient cycle failure")
    calls = 0

    def fake_cycle():
        nonlocal calls
        calls += 1

        if calls == 1:
            raise error

        worker.stop()

        return LineageInvalidationExecutionCycleResult(
            status=(
                worker_module
                .LineageInvalidationExecutionCycleStatus
                .IDLE
            ),
        )

    monkeypatch.setattr(
        worker_module,
        "run_lineage_invalidation_execution_cycle",
        fake_cycle,
    )
    monkeypatch.setattr(
        worker.stop_event,
        "wait",
        Mock(return_value=False),
    )

    worker.run()

    assert calls == 2
    assert reporter.cycle_started.call_count == 2
    reporter.cycle_failed.assert_called_once_with(error)
    reporter.cycle_succeeded.assert_called_once_with()
