from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
import base64

import scripts.alert_delivery_worker as worker_module
from scripts.alert_delivery_worker import (
    AlertDeliveryWorker,
    ClaimedDeliveryJob,
    WebhookDeliveryJob,
)
from src.models.alert_delivery import (
    ALERT_DELIVERY_DESTINATION_WEBHOOK,
    ALERT_DELIVERY_STATUS_PROCESSING,
)
from src.services.alert_delivery_runtime_service import (
    AlertDeliveryRuntimeError,
)
from src.services.webhook_delivery_transport_service import (
    PermanentWebhookDeliveryError,
    TransientWebhookDeliveryError,
    WebhookDeliveryResponse,
)
from src.services.webhook_secret_encryption_service import (
    WebhookSecretEncryptionError,
    encrypt_webhook_secret,
)


def _claimed_job(
    *,
    attempt_count: int = 1,
) -> ClaimedDeliveryJob:
    return ClaimedDeliveryJob(
        delivery_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        destination_type=(
            ALERT_DELIVERY_DESTINATION_WEBHOOK
        ),
        destination_id=uuid.uuid4(),
        delivery_key="delivery-key",
        payload={
            "event": "alert.created",
        },
        attempt_count=attempt_count,
    )


def _prepared_job(
    job: ClaimedDeliveryJob,
) -> WebhookDeliveryJob:
    return WebhookDeliveryJob(
        delivery_id=job.delivery_id,
        organization_id=job.organization_id,
        destination_id=job.destination_id,
        delivery_key=job.delivery_key,
        payload=dict(job.payload),
        attempt_count=job.attempt_count,
        url="https://example.com/webhook",
        secret_key="secret",
    )


def _worker_without_http_init() -> AlertDeliveryWorker:
    worker = AlertDeliveryWorker.__new__(
        AlertDeliveryWorker
    )
    worker._stopping = False

    return worker


def test_process_job_marks_successful_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()
    job = _claimed_job()
    prepared = _prepared_job(job)

    prepare = MagicMock(
        return_value=prepared
    )
    delivered = MagicMock()
    transient = MagicMock()
    permanent = MagicMock()

    monkeypatch.setattr(
        worker,
        "_prepare_webhook_job",
        prepare,
    )
    monkeypatch.setattr(
        worker,
        "_persist_delivered",
        delivered,
    )
    monkeypatch.setattr(
        worker,
        "_persist_transient_failure",
        transient,
    )
    monkeypatch.setattr(
        worker,
        "_persist_permanent_failure",
        permanent,
    )

    transport = MagicMock(
        return_value=WebhookDeliveryResponse(
            status_code=204,
        )
    )

    monkeypatch.setattr(
        worker_module,
        "deliver_webhook",
        transport,
    )

    worker._process_job(job)

    prepare.assert_called_once_with(job)

    transport.assert_called_once_with(
        url=prepared.url,
        secret_key=prepared.secret_key,
        delivery_id=str(prepared.delivery_id),
        delivery_key=prepared.delivery_key,
        payload=prepared.payload,
        timeout_seconds=(
            worker_module.HTTP_TIMEOUT_SECONDS
        ),
    )

    delivered.assert_called_once_with(job)
    transient.assert_not_called()
    permanent.assert_not_called()


def test_process_job_schedules_transient_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()
    job = _claimed_job()
    prepared = _prepared_job(job)

    monkeypatch.setattr(
        worker,
        "_prepare_webhook_job",
        MagicMock(return_value=prepared),
    )

    delivered = MagicMock()
    transient = MagicMock()
    permanent = MagicMock()

    monkeypatch.setattr(
        worker,
        "_persist_delivered",
        delivered,
    )
    monkeypatch.setattr(
        worker,
        "_persist_transient_failure",
        transient,
    )
    monkeypatch.setattr(
        worker,
        "_persist_permanent_failure",
        permanent,
    )

    monkeypatch.setattr(
        worker_module,
        "deliver_webhook",
        MagicMock(
            side_effect=(
                TransientWebhookDeliveryError(
                    "temporary failure"
                )
            )
        ),
    )

    worker._process_job(job)

    transient.assert_called_once_with(
        job,
        error="temporary failure",
    )
    delivered.assert_not_called()
    permanent.assert_not_called()


def test_process_job_marks_permanent_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()
    job = _claimed_job()
    prepared = _prepared_job(job)

    monkeypatch.setattr(
        worker,
        "_prepare_webhook_job",
        MagicMock(return_value=prepared),
    )

    delivered = MagicMock()
    transient = MagicMock()
    permanent = MagicMock()

    monkeypatch.setattr(
        worker,
        "_persist_delivered",
        delivered,
    )
    monkeypatch.setattr(
        worker,
        "_persist_transient_failure",
        transient,
    )
    monkeypatch.setattr(
        worker,
        "_persist_permanent_failure",
        permanent,
    )

    monkeypatch.setattr(
        worker_module,
        "deliver_webhook",
        MagicMock(
            side_effect=(
                PermanentWebhookDeliveryError(
                    "invalid destination"
                )
            )
        ),
    )

    worker._process_job(job)

    permanent.assert_called_once_with(
        job,
        error="invalid destination",
    )
    delivered.assert_not_called()
    transient.assert_not_called()


def test_process_job_discards_lost_claim_during_prepare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()
    job = _claimed_job()

    monkeypatch.setattr(
        worker,
        "_prepare_webhook_job",
        MagicMock(
            side_effect=AlertDeliveryRuntimeError(
                "AlertDelivery attempt is no longer current."
            )
        ),
    )

    transport = MagicMock()
    delivered = MagicMock()
    transient = MagicMock()
    permanent = MagicMock()

    monkeypatch.setattr(
        worker_module,
        "deliver_webhook",
        transport,
    )
    monkeypatch.setattr(
        worker,
        "_persist_delivered",
        delivered,
    )
    monkeypatch.setattr(
        worker,
        "_persist_transient_failure",
        transient,
    )
    monkeypatch.setattr(
        worker,
        "_persist_permanent_failure",
        permanent,
    )

    worker._process_job(job)

    transport.assert_not_called()
    delivered.assert_not_called()
    transient.assert_not_called()
    permanent.assert_not_called()


def test_process_job_terminalizes_permanent_prepare_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()
    job = _claimed_job()

    monkeypatch.setattr(
        worker,
        "_prepare_webhook_job",
        MagicMock(
            side_effect=(
                PermanentWebhookDeliveryError(
                    "Webhook destination is not active."
                )
            )
        ),
    )

    permanent = MagicMock()
    transport = MagicMock()

    monkeypatch.setattr(
        worker,
        "_persist_permanent_failure",
        permanent,
    )
    monkeypatch.setattr(
        worker_module,
        "deliver_webhook",
        transport,
    )

    worker._process_job(job)

    permanent.assert_called_once_with(
        job,
        error="Webhook destination is not active.",
    )
    transport.assert_not_called()


def test_process_job_retries_secret_decryption_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()
    job = _claimed_job()

    monkeypatch.setattr(
        worker,
        "_prepare_webhook_job",
        MagicMock(
            side_effect=WebhookSecretEncryptionError(
                "Webhook secret encryption key is not configured."
            )
        ),
    )

    delivered = MagicMock()
    transient = MagicMock()
    permanent = MagicMock()
    transport = MagicMock()

    monkeypatch.setattr(
        worker,
        "_persist_delivered",
        delivered,
    )
    monkeypatch.setattr(
        worker,
        "_persist_transient_failure",
        transient,
    )
    monkeypatch.setattr(
        worker,
        "_persist_permanent_failure",
        permanent,
    )
    monkeypatch.setattr(
        worker_module,
        "deliver_webhook",
        transport,
    )

    worker._process_job(job)

    transient.assert_called_once_with(
        job,
        error="Webhook preparation failed unexpectedly.",
    )
    permanent.assert_not_called()
    delivered.assert_not_called()
    transport.assert_not_called()


def test_run_once_drains_claimed_batch_after_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()

    first_job = _claimed_job()
    second_job = _claimed_job()

    finalize = MagicMock(return_value=0)
    claim_batch = MagicMock(
        return_value=(
            first_job,
            second_job,
        )
    )

    processed_jobs: list[ClaimedDeliveryJob] = []

    def process_job(job: ClaimedDeliveryJob) -> None:
        processed_jobs.append(job)

        if job == first_job:
            worker.stop()

    monkeypatch.setattr(
        worker,
        "_finalize_exhausted",
        finalize,
    )
    monkeypatch.setattr(
        worker,
        "_claim_batch",
        claim_batch,
    )
    monkeypatch.setattr(
        worker,
        "_process_job",
        process_job,
    )

    processed = worker.run_once()

    assert processed == 2
    assert processed_jobs == [
        first_job,
        second_job,
    ]

    finalize.assert_called_once_with()
    claim_batch.assert_called_once_with()


def test_claim_batch_commits_before_returning_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()

    delivery = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        destination_type=(
            ALERT_DELIVERY_DESTINATION_WEBHOOK
        ),
        destination_id=uuid.uuid4(),
        delivery_key="delivery-key",
        payload={
            "event": "alert.created",
        },
        attempt_count=2,
    )

    db = MagicMock()

    monkeypatch.setattr(
        worker_module,
        "SessionLocal",
        MagicMock(return_value=db),
    )

    claim = MagicMock(
        return_value=(delivery,)
    )

    monkeypatch.setattr(
        worker_module,
        "claim_alert_deliveries",
        claim,
    )

    jobs = worker._claim_batch()

    assert len(jobs) == 1

    job = jobs[0]

    assert job.delivery_id == delivery.id
    assert (
        job.organization_id
        == delivery.organization_id
    )
    assert (
        job.destination_id
        == delivery.destination_id
    )
    assert job.delivery_key == "delivery-key"
    assert job.attempt_count == 2
    assert job.payload == {
        "event": "alert.created",
    }

    claim.assert_called_once_with(
        db,
        limit=worker_module.BATCH_SIZE,
        max_attempts=worker_module.MAX_ATTEMPTS,
        claim_timeout_seconds=(
            worker_module.CLAIM_TIMEOUT_SECONDS
        ),
    )

    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()
    db.close.assert_called_once_with()


def test_claim_batch_rolls_back_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()

    db = MagicMock()

    monkeypatch.setattr(
        worker_module,
        "SessionLocal",
        MagicMock(return_value=db),
    )

    monkeypatch.setattr(
        worker_module,
        "claim_alert_deliveries",
        MagicMock(
            side_effect=RuntimeError(
                "database failure"
            )
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="database failure",
    ):
        worker._claim_batch()

    db.commit.assert_not_called()
    db.rollback.assert_called_once_with()
    db.close.assert_called_once_with()


def test_prepare_webhook_job_rejects_plaintext_only_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()
    job = _claimed_job(attempt_count=3)

    delivery = SimpleNamespace(
        id=job.delivery_id,
        status=ALERT_DELIVERY_STATUS_PROCESSING,
        attempt_count=3,
    )

    subscription = SimpleNamespace(
        id=job.destination_id,
        organization_id=job.organization_id,
        url="https://example.com/webhook",
        secret_key="secret",
        secret_ciphertext=None,
        secret_nonce=None,
        secret_encryption_version=None,
        is_active=True,
    )

    delivery_query = MagicMock()
    delivery_query.filter.return_value.one_or_none.return_value = (
        delivery
    )

    subscription_query = MagicMock()
    subscription_query.filter.return_value.one_or_none.return_value = (
        subscription
    )

    db = MagicMock()
    db.query.side_effect = [
        delivery_query,
        subscription_query,
    ]

    monkeypatch.setattr(
        worker_module,
        "SessionLocal",
        MagicMock(return_value=db),
    )

    mark_started = MagicMock()

    monkeypatch.setattr(
        worker_module,
        "mark_delivery_attempt_started",
        mark_started,
    )

    with pytest.raises(
        PermanentWebhookDeliveryError,
        match="storage representation is incomplete",
    ):
        worker._prepare_webhook_job(job)

    mark_started.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_called_once_with()
    db.close.assert_called_once_with()


def test_prepare_webhook_job_decrypts_encrypted_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()
    job = _claimed_job(attempt_count=3)

    encryption_key = base64.urlsafe_b64encode(
        b"\x42" * 32
    ).decode("ascii")

    encrypted = encrypt_webhook_secret(
        "encrypted-signing-secret",
        organization_id=job.organization_id,
        subscription_id=job.destination_id,
        encoded_encryption_key=encryption_key,
    )

    delivery = SimpleNamespace(
        id=job.delivery_id,
        status=ALERT_DELIVERY_STATUS_PROCESSING,
        attempt_count=3,
    )

    subscription = SimpleNamespace(
        id=job.destination_id,
        organization_id=job.organization_id,
        url="https://example.com/webhook",
        secret_key=None,
        secret_ciphertext=encrypted.ciphertext,
        secret_nonce=encrypted.nonce,
        secret_encryption_version=encrypted.version,
        is_active=True,
    )

    delivery_query = MagicMock()
    delivery_query.filter.return_value.one_or_none.return_value = (
        delivery
    )

    subscription_query = MagicMock()
    subscription_query.filter.return_value.one_or_none.return_value = (
        subscription
    )

    db = MagicMock()
    db.query.side_effect = [
        delivery_query,
        subscription_query,
    ]

    monkeypatch.setattr(
        worker_module,
        "SessionLocal",
        MagicMock(return_value=db),
    )

    monkeypatch.setattr(
        worker_module.settings,
        "WEBHOOK_SECRET_ENCRYPTION_KEY",
        encryption_key,
    )

    mark_started = MagicMock()

    monkeypatch.setattr(
        worker_module,
        "mark_delivery_attempt_started",
        mark_started,
    )

    prepared = worker._prepare_webhook_job(job)

    assert (
        prepared.secret_key
        == "encrypted-signing-secret"
    )

    mark_started.assert_called_once_with(
        db,
        delivery_id=job.delivery_id,
        expected_attempt_count=3,
    )

    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()
    db.close.assert_called_once_with()


def test_prepare_webhook_job_rejects_inactive_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()
    job = _claimed_job()

    delivery = SimpleNamespace(
        id=job.delivery_id,
        status=ALERT_DELIVERY_STATUS_PROCESSING,
        attempt_count=1,
    )

    subscription = SimpleNamespace(
        id=job.destination_id,
        organization_id=job.organization_id,
        url="https://example.com/webhook",
        secret_key="secret",
        is_active=False,
    )

    delivery_query = MagicMock()
    delivery_query.filter.return_value.one_or_none.return_value = (
        delivery
    )

    subscription_query = MagicMock()
    subscription_query.filter.return_value.one_or_none.return_value = (
        subscription
    )

    db = MagicMock()
    db.query.side_effect = [
        delivery_query,
        subscription_query,
    ]

    monkeypatch.setattr(
        worker_module,
        "SessionLocal",
        MagicMock(return_value=db),
    )

    mark_started = MagicMock()

    monkeypatch.setattr(
        worker_module,
        "mark_delivery_attempt_started",
        mark_started,
    )

    with pytest.raises(
        PermanentWebhookDeliveryError,
        match="not active",
    ):
        worker._prepare_webhook_job(job)

    mark_started.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_called_once_with()
    db.close.assert_called_once_with()


def test_prepare_webhook_job_rejects_stale_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()
    job = _claimed_job(attempt_count=1)

    delivery = SimpleNamespace(
        id=job.delivery_id,
        status=ALERT_DELIVERY_STATUS_PROCESSING,
        attempt_count=2,
    )

    delivery_query = MagicMock()
    delivery_query.filter.return_value.one_or_none.return_value = (
        delivery
    )

    db = MagicMock()
    db.query.return_value = delivery_query

    monkeypatch.setattr(
        worker_module,
        "SessionLocal",
        MagicMock(return_value=db),
    )

    mark_started = MagicMock()

    monkeypatch.setattr(
        worker_module,
        "mark_delivery_attempt_started",
        mark_started,
    )

    with pytest.raises(
        AlertDeliveryRuntimeError,
        match="no longer current",
    ):
        worker._prepare_webhook_job(job)

    mark_started.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_called_once_with()
    db.close.assert_called_once_with()

def test_finalize_exhausted_commits_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()

    db = MagicMock()

    monkeypatch.setattr(
        worker_module,
        "SessionLocal",
        MagicMock(return_value=db),
    )

    finalized = (
        SimpleNamespace(id=uuid.uuid4()),
        SimpleNamespace(id=uuid.uuid4()),
    )

    finalize = MagicMock(
        return_value=finalized
    )

    monkeypatch.setattr(
        worker_module,
        "finalize_exhausted_alert_deliveries",
        finalize,
    )

    count = worker._finalize_exhausted()

    assert count == 2

    finalize.assert_called_once_with(
        db,
        limit=worker_module.BATCH_SIZE,
        max_attempts=worker_module.MAX_ATTEMPTS,
        claim_timeout_seconds=(
            worker_module.CLAIM_TIMEOUT_SECONDS
        ),
    )

    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()
    db.close.assert_called_once_with()


def test_finalize_exhausted_rolls_back_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()

    db = MagicMock()

    monkeypatch.setattr(
        worker_module,
        "SessionLocal",
        MagicMock(return_value=db),
    )

    monkeypatch.setattr(
        worker_module,
        "finalize_exhausted_alert_deliveries",
        MagicMock(
            side_effect=RuntimeError(
                "recovery failure"
            )
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="recovery failure",
    ):
        worker._finalize_exhausted()

    db.commit.assert_not_called()
    db.rollback.assert_called_once_with()
    db.close.assert_called_once_with()


def test_run_reports_successful_operational_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter = Mock()
    worker = AlertDeliveryWorker(
        operational_reporter=reporter,
    )

    monkeypatch.setattr(
        worker,
        "validate_config",
        Mock(),
    )

    def run_once() -> int:
        worker.stop()
        return 0

    monkeypatch.setattr(
        worker,
        "run_once",
        run_once,
    )
    monkeypatch.setattr(
        worker_module.time,
        "sleep",
        Mock(),
    )

    worker.run()

    reporter.cycle_started.assert_called_once_with()
    reporter.cycle_succeeded.assert_called_once_with()
    reporter.cycle_failed.assert_not_called()


def test_run_reports_operational_cycle_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter = Mock()
    worker = AlertDeliveryWorker(
        operational_reporter=reporter,
    )

    monkeypatch.setattr(
        worker,
        "validate_config",
        Mock(),
    )
    monkeypatch.setattr(
        worker,
        "run_once",
        Mock(
            side_effect=RuntimeError(
                "claim infrastructure failed"
            )
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="claim infrastructure failed",
    ):
        worker.run()

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
    ) == "claim infrastructure failed"

    reporter.cycle_succeeded.assert_not_called()


def test_run_preserves_primary_failure_when_failure_reporting_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter = Mock()
    reporter.cycle_failed.side_effect = RuntimeError(
        "reporting failed"
    )

    worker = AlertDeliveryWorker(
        operational_reporter=reporter,
    )

    monkeypatch.setattr(
        worker,
        "validate_config",
        Mock(),
    )
    monkeypatch.setattr(
        worker,
        "run_once",
        Mock(
            side_effect=RuntimeError(
                "primary worker failure"
            )
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="primary worker failure",
    ):
        worker.run()

    reporter.cycle_started.assert_called_once_with()
    reporter.cycle_failed.assert_called_once()
    reporter.cycle_succeeded.assert_not_called()


def test_main_registers_operational_reporter_and_runs_worker(
    monkeypatch: pytest.MonkeyPatch,
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
    worker_factory = Mock(
        return_value=worker
    )

    monkeypatch.setattr(
        worker_module,
        "WorkerOperationalReporter",
        reporter_factory,
    )
    monkeypatch.setattr(
        worker_module,
        "WorkerOperationalHeartbeat",
        heartbeat_factory,
    )
    monkeypatch.setattr(
        worker_module,
        "AlertDeliveryWorker",
        worker_factory,
    )
    monkeypatch.setattr(
        worker_module.signal,
        "signal",
        Mock(),
    )

    worker_module.main()

    reporter_factory.assert_called_once_with(
        worker_kind=(
            worker_module
            .WORKER_KIND_ALERT_DELIVERY
        ),
        worker_instance_id=(
            worker_module.WORKER_ID
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
    worker.run.assert_called_once_with()
    heartbeat.raise_if_failed.assert_called_once_with()
    heartbeat.stop.assert_called_once_with()


def test_main_stops_heartbeat_when_worker_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter = Mock()
    heartbeat = Mock()
    worker = Mock()
    worker.run.side_effect = RuntimeError(
        "worker failed"
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
        worker_module,
        "AlertDeliveryWorker",
        Mock(return_value=worker),
    )
    monkeypatch.setattr(
        worker_module.signal,
        "signal",
        Mock(),
    )

    with pytest.raises(
        RuntimeError,
        match="worker failed",
    ):
        worker_module.main()

    heartbeat.start.assert_called_once_with()
    heartbeat.raise_if_failed.assert_not_called()
    heartbeat.stop.assert_called_once_with()


def test_process_job_never_persists_raw_unexpected_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker_without_http_init()
    job = _claimed_job()
    monkeypatch.setattr(
        worker,
        "_prepare_webhook_job",
        MagicMock(return_value=_prepared_job(job)),
    )
    monkeypatch.setattr(
        worker_module,
        "deliver_webhook",
        MagicMock(side_effect=RuntimeError("DATABASE_URL=postgres://secret")),
    )
    transient = MagicMock()
    monkeypatch.setattr(worker, "_persist_transient_failure", transient)

    worker._process_job(job)

    transient.assert_called_once_with(
        job,
        error="Webhook transport failed unexpectedly.",
    )
