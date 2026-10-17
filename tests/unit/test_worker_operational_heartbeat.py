import threading
import time
from unittest.mock import Mock

import pytest

from src.services.worker_operational_heartbeat import (
    WorkerOperationalHeartbeat,
)


def test_rejects_non_positive_interval() -> None:
    with pytest.raises(
        ValueError,
        match="interval_seconds must be greater than zero",
    ):
        WorkerOperationalHeartbeat(
            Mock(),
            interval_seconds=0,
        )


def test_emits_heartbeat_until_stopped() -> None:
    heartbeat_seen = threading.Event()

    def heartbeat() -> None:
        heartbeat_seen.set()

    runner = WorkerOperationalHeartbeat(
        heartbeat,
        interval_seconds=0.01,
    )

    runner.start()

    try:
        assert heartbeat_seen.wait(
            timeout=1.0
        )
        runner.raise_if_failed()
    finally:
        runner.stop()


def test_stop_prevents_future_heartbeats() -> None:
    heartbeat = Mock()

    runner = WorkerOperationalHeartbeat(
        heartbeat,
        interval_seconds=0.01,
    )

    runner.start()

    deadline = time.monotonic() + 1.0

    while (
        heartbeat.call_count == 0
        and time.monotonic() < deadline
    ):
        time.sleep(0.005)

    assert heartbeat.call_count > 0

    runner.stop()

    count_after_stop = heartbeat.call_count

    time.sleep(0.03)

    assert heartbeat.call_count == count_after_stop


def test_reporting_failure_is_retained_and_stops_loop() -> None:
    failure = RuntimeError(
        "heartbeat persistence failed"
    )
    attempted = threading.Event()

    def heartbeat() -> None:
        attempted.set()
        raise failure

    runner = WorkerOperationalHeartbeat(
        heartbeat,
        interval_seconds=0.01,
    )

    runner.start()

    try:
        assert attempted.wait(
            timeout=1.0
        )

        deadline = time.monotonic() + 1.0

        while (
            runner.last_error is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)

        assert runner.last_error is failure

        with pytest.raises(
            RuntimeError,
            match="worker operational heartbeat failed",
        ) as exc_info:
            runner.raise_if_failed()

        assert exc_info.value.__cause__ is failure
    finally:
        runner.stop()


def test_reporting_failure_requests_cooperative_shutdown() -> None:
    failure = RuntimeError("heartbeat persistence failed")
    shutdown_requested = threading.Event()
    heartbeat = Mock(side_effect=failure)
    on_failure = Mock(side_effect=shutdown_requested.set)

    runner = WorkerOperationalHeartbeat(
        heartbeat,
        interval_seconds=0.01,
        on_failure=on_failure,
    )
    runner.start()

    try:
        assert shutdown_requested.wait(timeout=1.0)
        assert runner.last_error is failure
        on_failure.assert_called_once_with()
        with pytest.raises(
            RuntimeError,
            match="worker operational heartbeat failed",
        ) as exc_info:
            runner.raise_if_failed()
        assert exc_info.value.__cause__ is failure
    finally:
        runner.stop()

    # A failed heartbeat cannot trigger repeated restart requests.
    assert heartbeat.call_count == 1
    on_failure.assert_called_once_with()


def test_normal_stop_does_not_request_failure_shutdown() -> None:
    heartbeat_seen = threading.Event()
    on_failure = Mock()
    runner = WorkerOperationalHeartbeat(
        heartbeat_seen.set,
        interval_seconds=0.01,
        on_failure=on_failure,
    )
    runner.start()
    try:
        assert heartbeat_seen.wait(timeout=1.0)
    finally:
        runner.stop()

    runner.raise_if_failed()
    on_failure.assert_not_called()


def test_failure_callback_error_preserves_original_heartbeat_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failure = RuntimeError("heartbeat write failed")
    callback_attempted = threading.Event()

    def on_failure() -> None:
        callback_attempted.set()
        raise RuntimeError("shutdown notification failed")

    runner = WorkerOperationalHeartbeat(
        Mock(side_effect=failure),
        interval_seconds=0.01,
        on_failure=on_failure,
    )
    runner.start()

    try:
        assert callback_attempted.wait(timeout=1.0)
        with pytest.raises(
            RuntimeError,
            match="worker operational heartbeat failed",
        ) as exc_info:
            runner.raise_if_failed()
        assert exc_info.value.__cause__ is failure
    finally:
        runner.stop()

    assert "shutdown callback failed" in caplog.text


def test_cannot_start_twice_while_running() -> None:
    runner = WorkerOperationalHeartbeat(
        Mock(),
        interval_seconds=1.0,
    )

    runner.start()

    try:
        with pytest.raises(
            RuntimeError,
            match="already running",
        ):
            runner.start()
    finally:
        runner.stop()
