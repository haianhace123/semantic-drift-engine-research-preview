from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

from scripts import email_worker


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


class FakeHttpClient:
    def __init__(
        self,
        response: FakeResponse,
    ) -> None:
        self.response = response
        self.calls = []

    async def post(
        self,
        url,
        *,
        headers,
        json,
    ):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
            }
        )

        return self.response


class FakeRedis:
    async def aclose(self) -> None:
        return None


def build_worker(
    response: FakeResponse,
) -> tuple[
    email_worker.EmailWorker,
    FakeHttpClient,
]:
    worker = object.__new__(
        email_worker.EmailWorker
    )

    client = FakeHttpClient(
        response
    )

    worker.http = client
    worker.redis = FakeRedis()

    return worker, client


def test_deliver_sends_resend_idempotency_header(
    monkeypatch,
):
    monkeypatch.setattr(
        email_worker,
        "RESEND_API_KEY",
        "test-api-key",
    )

    monkeypatch.setattr(
        email_worker,
        "EMAIL_FROM",
        "Temporal Intelligence <noreply@example.com>",
    )

    monkeypatch.setattr(
        email_worker,
        "EMAIL_REPLY_TO",
        "",
    )

    worker, client = build_worker(
        FakeResponse(
            200,
            {
                "id": "email-123",
            },
        )
    )

    job = email_worker.EmailJob(
        job_id="job-123",
        email_type="verification",
        recipients=[
            "user@example.com",
        ],
        subject="Verify your account",
        html="<p>123456</p>",
        text="123456",
        idempotency_key="verification-user-123",
        attempt=0,
        max_attempts=5,
    )

    asyncio.run(
        worker._deliver(
            job
        )
    )

    assert len(
        client.calls
    ) == 1

    call = client.calls[0]

    assert (
        call["url"]
        == "https://api.resend.com/emails"
    )

    assert (
        call["headers"][
            "Authorization"
        ]
        == "Bearer test-api-key"
    )

    assert (
        call["headers"][
            "Idempotency-Key"
        ]
        == "verification-user-123"
    )

    assert (
        "X-SDE-Idempotency-Key"
        not in call["headers"]
    )

    assert (
        call["json"]["from"]
        == "Temporal Intelligence <noreply@example.com>"
    )

    assert (
        call["json"]["to"]
        == ["user@example.com"]
    )

    assert (
        call["json"]["subject"]
        == "Verify your account"
    )

    assert (
        call["json"]["html"]
        == "<p>123456</p>"
    )

    assert (
        call["json"]["text"]
        == "123456"
    )


@pytest.mark.parametrize(
    "status_code",
    [
        408,
        409,
        429,
        500,
        502,
        503,
        504,
    ],
)
def test_deliver_classifies_retryable_resend_errors(
    monkeypatch,
    status_code,
):
    monkeypatch.setattr(
        email_worker,
        "RESEND_API_KEY",
        "test-api-key",
    )

    monkeypatch.setattr(
        email_worker,
        "EMAIL_FROM",
        "noreply@example.com",
    )

    monkeypatch.setattr(
        email_worker,
        "EMAIL_REPLY_TO",
        "",
    )

    worker, _ = build_worker(
        FakeResponse(
            status_code,
            {
                "message": "temporary failure",
            },
        )
    )

    job = email_worker.EmailJob(
        job_id="job-123",
        email_type="transactional",
        recipients=[
            "user@example.com",
        ],
        subject="Subject",
        html="<p>Body</p>",
        text=None,
        idempotency_key="job-123",
        attempt=0,
        max_attempts=5,
    )

    with pytest.raises(
        email_worker.TransientEmailError
    ):
        asyncio.run(
            worker._deliver(
                job
            )
        )


def test_deliver_classifies_non_retryable_resend_error(
    monkeypatch,
):
    monkeypatch.setattr(
        email_worker,
        "RESEND_API_KEY",
        "test-api-key",
    )

    monkeypatch.setattr(
        email_worker,
        "EMAIL_FROM",
        "noreply@example.com",
    )

    monkeypatch.setattr(
        email_worker,
        "EMAIL_REPLY_TO",
        "",
    )

    worker, _ = build_worker(
        FakeResponse(
            422,
            {
                "message": "invalid recipient",
            },
        )
    )

    job = email_worker.EmailJob(
        job_id="job-123",
        email_type="transactional",
        recipients=[
            "invalid@example.com",
        ],
        subject="Subject",
        html="<p>Body</p>",
        text=None,
        idempotency_key="job-123",
        attempt=0,
        max_attempts=5,
    )

    with pytest.raises(
        email_worker.PermanentEmailError
    ):
        asyncio.run(
            worker._deliver(
                job
            )
        )


def test_deliver_does_not_expose_provider_response_in_error(
    monkeypatch,
):
    monkeypatch.setattr(
        email_worker,
        "RESEND_API_KEY",
        "test-api-key",
    )

    monkeypatch.setattr(
        email_worker,
        "EMAIL_FROM",
        "noreply@example.com",
    )

    monkeypatch.setattr(
        email_worker,
        "EMAIL_REPLY_TO",
        "",
    )

    sensitive_value = "private-recipient@example.com"

    worker, _ = build_worker(
        FakeResponse(
            422,
            {
                "message": sensitive_value,
            },
        )
    )

    job = email_worker.EmailJob(
        job_id="job-123",
        email_type="transactional",
        recipients=[
            "user@example.com",
        ],
        subject="Subject",
        html="<p>Body</p>",
        text=None,
        idempotency_key="job-123",
        attempt=0,
        max_attempts=5,
    )

    with pytest.raises(
        email_worker.PermanentEmailError
    ) as exc_info:
        asyncio.run(
            worker._deliver(
                job
            )
        )

    message = str(
        exc_info.value
    )

    assert message == "Resend returned HTTP 422"
    assert sensitive_value not in message


def test_retry_delay_uses_bounded_exponential_backoff(
    monkeypatch,
):
    monkeypatch.setattr(
        email_worker,
        "RETRY_BASE_SECONDS",
        5,
    )

    monkeypatch.setattr(
        email_worker,
        "RETRY_MAX_SECONDS",
        300,
    )

    assert (
        email_worker.retry_delay(1)
        == 5
    )

    assert (
        email_worker.retry_delay(2)
        == 10
    )

    assert (
        email_worker.retry_delay(3)
        == 20
    )

    assert (
        email_worker.retry_delay(20)
        == 300
    )


def test_operational_reporting_runs_off_event_loop(
    monkeypatch,
) -> None:
    reporter = Mock()
    worker = object.__new__(
        email_worker.EmailWorker
    )
    worker._operational_reporter = reporter

    calls = []

    async def fake_to_thread(
        operation,
        *args,
    ):
        calls.append(
            (
                operation,
                args,
            )
        )

    monkeypatch.setattr(
        email_worker.asyncio,
        "to_thread",
        fake_to_thread,
    )

    asyncio.run(
        worker._report_operational(
            reporter.cycle_failed,
            "failure",
        )
    )

    assert calls == [
        (
            reporter.cycle_failed,
            ("failure",),
        )
    ]


def test_start_reports_successful_operational_cycle(
    monkeypatch,
) -> None:
    reporter = Mock()
    worker = object.__new__(
        email_worker.EmailWorker
    )

    worker._operational_reporter = reporter
    worker.stop_event = asyncio.Event()

    http = Mock()
    http.aclose = Mock()

    redis_client = Mock()
    redis_client.aclose = Mock()

    worker.http = http
    worker.redis = redis_client

    async def no_op() -> None:
        return None

    async def consume_once() -> None:
        worker.stop()

    async def fake_report(
        operation,
        *args,
    ) -> None:
        operation(
            *args
        )

    monkeypatch.setattr(
        worker,
        "_validate_config",
        Mock(),
    )
    monkeypatch.setattr(
        worker,
        "_ensure_consumer_group",
        no_op,
    )
    monkeypatch.setattr(
        worker,
        "_recover_pending",
        no_op,
    )
    monkeypatch.setattr(
        worker,
        "_promote_due_retries",
        no_op,
    )
    monkeypatch.setattr(
        worker,
        "_recover_pending_once",
        no_op,
    )
    monkeypatch.setattr(
        worker,
        "_consume_once",
        consume_once,
    )
    monkeypatch.setattr(
        worker,
        "_report_operational",
        fake_report,
    )

    async def run() -> None:
        async def close_http() -> None:
            return None

        async def close_redis() -> None:
            return None

        worker.http.aclose = close_http
        worker.redis.aclose = close_redis

        await worker.start()

    asyncio.run(
        run()
    )

    reporter.cycle_started.assert_called_once_with()
    reporter.cycle_succeeded.assert_called_once_with()
    reporter.cycle_failed.assert_not_called()


def test_start_reports_operational_cycle_failure(
    monkeypatch,
) -> None:
    reporter = Mock()
    worker = object.__new__(
        email_worker.EmailWorker
    )

    worker._operational_reporter = reporter
    worker.stop_event = asyncio.Event()

    async def no_op() -> None:
        return None

    async def fail_cycle() -> None:
        raise RuntimeError(
            "redis infrastructure failed"
        )

    async def fake_report(
        operation,
        *args,
    ) -> None:
        operation(
            *args
        )

    async def close() -> None:
        return None

    worker.http = Mock()
    worker.http.aclose = close

    worker.redis = Mock()
    worker.redis.aclose = close

    monkeypatch.setattr(
        worker,
        "_validate_config",
        Mock(),
    )
    monkeypatch.setattr(
        worker,
        "_ensure_consumer_group",
        no_op,
    )
    monkeypatch.setattr(
        worker,
        "_recover_pending",
        no_op,
    )
    monkeypatch.setattr(
        worker,
        "_promote_due_retries",
        fail_cycle,
    )
    monkeypatch.setattr(
        worker,
        "_report_operational",
        fake_report,
    )

    with pytest.raises(
        RuntimeError,
        match="redis infrastructure failed",
    ):
        asyncio.run(
            worker.start()
        )

    reporter.cycle_started.assert_called_once_with()
    reporter.cycle_failed.assert_called_once()

    reported_error = (
        reporter.cycle_failed.call_args.args[0]
    )

    assert isinstance(
        reported_error,
        RuntimeError,
    )
    assert str(
        reported_error
    ) == "redis infrastructure failed"

    reporter.cycle_succeeded.assert_not_called()


def test_start_preserves_primary_failure_when_failure_reporting_fails(
    monkeypatch,
) -> None:
    reporter = Mock()
    worker = object.__new__(
        email_worker.EmailWorker
    )

    worker._operational_reporter = reporter
    worker.stop_event = asyncio.Event()

    async def no_op() -> None:
        return None

    async def fail_cycle() -> None:
        raise RuntimeError(
            "primary email worker failure"
        )

    report_calls = 0

    async def fake_report(
        operation,
        *args,
    ) -> None:
        nonlocal report_calls
        report_calls += 1

        if report_calls == 2:
            raise RuntimeError(
                "reporting failed"
            )

        operation(
            *args
        )

    async def close() -> None:
        return None

    worker.http = Mock()
    worker.http.aclose = close

    worker.redis = Mock()
    worker.redis.aclose = close

    monkeypatch.setattr(
        worker,
        "_validate_config",
        Mock(),
    )
    monkeypatch.setattr(
        worker,
        "_ensure_consumer_group",
        no_op,
    )
    monkeypatch.setattr(
        worker,
        "_recover_pending",
        no_op,
    )
    monkeypatch.setattr(
        worker,
        "_promote_due_retries",
        fail_cycle,
    )
    monkeypatch.setattr(
        worker,
        "_report_operational",
        fake_report,
    )

    with pytest.raises(
        RuntimeError,
        match="primary email worker failure",
    ):
        asyncio.run(
            worker.start()
        )

    reporter.cycle_started.assert_called_once_with()
    reporter.cycle_succeeded.assert_not_called()


def test_main_registers_operational_reporter_and_runs_worker(
    monkeypatch,
) -> None:
    reporter = Mock()
    reporter_factory = Mock(
        return_value=reporter
    )

    heartbeat = Mock()
    heartbeat_factory = Mock(
        return_value=heartbeat
    )

    worker = Mock()

    async def start() -> None:
        return None

    worker.start = start

    worker_factory = Mock(
        return_value=worker
    )

    async def fake_to_thread(
        operation,
        *args,
    ):
        return operation(
            *args
        )

    class FakeLoop:
        def add_signal_handler(
            self,
            *args,
        ) -> None:
            return None

    monkeypatch.setattr(
        email_worker,
        "WorkerOperationalReporter",
        reporter_factory,
    )
    monkeypatch.setattr(
        email_worker,
        "WorkerOperationalHeartbeat",
        heartbeat_factory,
    )
    monkeypatch.setattr(
        email_worker,
        "EmailWorker",
        worker_factory,
    )
    monkeypatch.setattr(
        email_worker.asyncio,
        "to_thread",
        fake_to_thread,
    )
    monkeypatch.setattr(
        email_worker.asyncio,
        "get_running_loop",
        lambda: FakeLoop(),
    )

    asyncio.run(
        email_worker.main()
    )

    reporter_factory.assert_called_once_with(
        worker_kind=(
            email_worker.WORKER_KIND_EMAIL
        ),
        worker_instance_id=(
            email_worker.WORKER_ID
        ),
    )
    reporter.started.assert_called_once_with()
    heartbeat_factory.assert_called_once_with(
        reporter.heartbeat,
    )
    heartbeat.start.assert_called_once_with()

    worker_factory.assert_called_once_with(
        operational_reporter=reporter,
    )
    heartbeat.raise_if_failed.assert_called_once_with()
    heartbeat.stop.assert_called_once_with()


def test_main_stops_heartbeat_when_worker_fails(
    monkeypatch,
) -> None:
    reporter = Mock()
    heartbeat = Mock()
    worker = Mock()

    async def start() -> None:
        raise RuntimeError(
            "email worker failed"
        )

    worker.start = start

    async def fake_to_thread(
        operation,
        *args,
    ):
        return operation(
            *args
        )

    class FakeLoop:
        def add_signal_handler(
            self,
            *args,
        ) -> None:
            return None

    monkeypatch.setattr(
        email_worker,
        "WorkerOperationalReporter",
        Mock(return_value=reporter),
    )
    monkeypatch.setattr(
        email_worker,
        "WorkerOperationalHeartbeat",
        Mock(return_value=heartbeat),
    )
    monkeypatch.setattr(
        email_worker,
        "EmailWorker",
        Mock(return_value=worker),
    )
    monkeypatch.setattr(
        email_worker.asyncio,
        "to_thread",
        fake_to_thread,
    )
    monkeypatch.setattr(
        email_worker.asyncio,
        "get_running_loop",
        lambda: FakeLoop(),
    )

    with pytest.raises(
        RuntimeError,
        match="email worker failed",
    ):
        asyncio.run(
            email_worker.main()
        )

    heartbeat.start.assert_called_once_with()
    heartbeat.raise_if_failed.assert_not_called()
    heartbeat.stop.assert_called_once_with()


def test_recover_pending_propagates_infrastructure_failure() -> None:
    worker = object.__new__(
        email_worker.EmailWorker
    )

    class FailingRedis:
        async def xautoclaim(
            self,
            *args,
            **kwargs,
        ):
            raise RuntimeError(
                "redis recovery failed"
            )

    worker.redis = FailingRedis()

    with pytest.raises(
        RuntimeError,
        match="redis recovery failed",
    ):
        asyncio.run(
            worker._recover_pending()
        )


def test_pending_recovery_failure_is_operational_cycle_failure(
    monkeypatch,
) -> None:
    reporter = Mock()
    worker = object.__new__(
        email_worker.EmailWorker
    )

    worker._operational_reporter = reporter
    worker.stop_event = asyncio.Event()

    recovery_calls = 0

    async def recover_pending() -> None:
        nonlocal recovery_calls
        recovery_calls += 1

        if recovery_calls > 1:
            raise RuntimeError(
                "redis recovery failed"
            )

    async def no_op() -> None:
        return None

    async def fake_report(
        operation,
        *args,
    ) -> None:
        operation(
            *args
        )

    async def close() -> None:
        return None

    worker.http = Mock()
    worker.http.aclose = close

    worker.redis = Mock()
    worker.redis.aclose = close

    monkeypatch.setattr(
        worker,
        "_validate_config",
        Mock(),
    )
    monkeypatch.setattr(
        worker,
        "_ensure_consumer_group",
        no_op,
    )
    monkeypatch.setattr(
        worker,
        "_recover_pending",
        recover_pending,
    )
    monkeypatch.setattr(
        worker,
        "_promote_due_retries",
        no_op,
    )
    monkeypatch.setattr(
        worker,
        "_report_operational",
        fake_report,
    )

    with pytest.raises(
        RuntimeError,
        match="redis recovery failed",
    ):
        asyncio.run(
            worker.start()
        )

    assert recovery_calls == 2
    reporter.cycle_started.assert_called_once_with()
    reporter.cycle_failed.assert_called_once()

    reported_error = (
        reporter.cycle_failed.call_args.args[0]
    )

    assert isinstance(
        reported_error,
        RuntimeError,
    )
    assert str(
        reported_error
    ) == "redis recovery failed"

    reporter.cycle_succeeded.assert_not_called()
