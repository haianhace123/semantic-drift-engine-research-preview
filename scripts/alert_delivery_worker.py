"""
Production worker for durable Alert webhook deliveries.

Processing contract:

TX1:
- claim ready AlertDelivery rows;
- copy claimed delivery identity into immutable jobs;
- commit claim.

For each job:

TX2:
- load current processing attempt;
- validate the webhook destination;
- snapshot URL and signing secret into the job;
- record last_attempt_at;
- commit.

HTTP:
- execute webhook request outside every database transaction.

TX3:
- persist delivered, retryable, or failed result;
- commit.

attempt_count is the fencing token. A stale worker cannot mutate a
delivery after a newer worker has reclaimed it.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from dataclasses import dataclass
from typing import Any


from src.core.config import settings
from src.core.database import SessionLocal
from src.models.worker_operational_state import (
    WORKER_KIND_ALERT_DELIVERY,
)
from src.services.worker_operational_heartbeat import (
    WorkerOperationalHeartbeat,
)
from src.services.worker_operational_reporter import (
    WorkerOperationalReporter,
)
from src.services.worker_runtime_identity import (
    create_worker_instance_id,
)
from src.models.alert_delivery import (
    ALERT_DELIVERY_DESTINATION_WEBHOOK,
    ALERT_DELIVERY_STATUS_PROCESSING,
    AlertDelivery,
)
from src.models.webhook_subscription import WebhookSubscription
from src.services.alert_delivery_runtime_service import (
    DEFAULT_CLAIM_TIMEOUT_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_RETRY_BASE_SECONDS,
    DEFAULT_RETRY_MAX_SECONDS,
    AlertDeliveryRuntimeError,
    claim_alert_deliveries,
    finalize_exhausted_alert_deliveries,
    mark_delivery_attempt_started,
    mark_delivery_delivered,
    mark_delivery_failed,
    mark_delivery_retryable,
)
from src.services.webhook_delivery_transport_service import (
    DEFAULT_WEBHOOK_TIMEOUT_SECONDS,
    PermanentWebhookDeliveryError,
    TransientWebhookDeliveryError,
    deliver_webhook,
)
from src.services.webhook_secret_encryption_service import (
    EncryptedWebhookSecret,
    decrypt_webhook_secret,
)


SERVICE_NAME = "semantic-drift-alert-delivery-worker"

WORKER_ID = create_worker_instance_id()

POLL_SECONDS = float(
    os.getenv(
        "ALERT_DELIVERY_WORKER_POLL_SECONDS",
        "2",
    )
)

BATCH_SIZE = int(
    os.getenv(
        "ALERT_DELIVERY_WORKER_BATCH_SIZE",
        "10",
    )
)

MAX_ATTEMPTS = int(
    os.getenv(
        "ALERT_DELIVERY_WORKER_MAX_ATTEMPTS",
        str(DEFAULT_MAX_ATTEMPTS),
    )
)

CLAIM_TIMEOUT_SECONDS = int(
    os.getenv(
        "ALERT_DELIVERY_WORKER_CLAIM_TIMEOUT_SECONDS",
        str(DEFAULT_CLAIM_TIMEOUT_SECONDS),
    )
)

RETRY_BASE_SECONDS = int(
    os.getenv(
        "ALERT_DELIVERY_WORKER_RETRY_BASE_SECONDS",
        str(DEFAULT_RETRY_BASE_SECONDS),
    )
)

RETRY_MAX_SECONDS = int(
    os.getenv(
        "ALERT_DELIVERY_WORKER_RETRY_MAX_SECONDS",
        str(DEFAULT_RETRY_MAX_SECONDS),
    )
)

HTTP_TIMEOUT_SECONDS = float(
    os.getenv(
        "ALERT_DELIVERY_WORKER_HTTP_TIMEOUT",
        str(DEFAULT_WEBHOOK_TIMEOUT_SECONDS),
    )
)


logger = logging.getLogger(SERVICE_NAME)

if not logger.handlers:
    handler = logging.StreamHandler()

    handler.setFormatter(
        logging.Formatter(
            (
                "%(asctime)sZ "
                "level=%(levelname)s "
                "service=%(name)s "
                "message=%(message)s"
            ),
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )

    logger.addHandler(handler)

logger.setLevel(
    os.getenv(
        "LOG_LEVEL",
        "INFO",
    ).upper()
)

logger.propagate = False


@dataclass(frozen=True, slots=True)
class ClaimedDeliveryJob:
    delivery_id: uuid.UUID
    organization_id: uuid.UUID
    destination_type: str
    destination_id: uuid.UUID
    delivery_key: str
    payload: dict[str, Any]
    attempt_count: int


@dataclass(frozen=True, slots=True)
class WebhookDeliveryJob:
    delivery_id: uuid.UUID
    organization_id: uuid.UUID
    destination_id: uuid.UUID
    delivery_key: str
    payload: dict[str, Any]
    attempt_count: int
    url: str
    secret_key: str


def _resolve_webhook_secret(
    subscription: WebhookSubscription,
) -> str:
    ciphertext = subscription.secret_ciphertext
    nonce = subscription.secret_nonce
    version = subscription.secret_encryption_version

    if any(
        value is None
        for value in (
            ciphertext,
            nonce,
            version,
        )
    ):
        raise PermanentWebhookDeliveryError(
            "Webhook secret storage representation is incomplete."
        )

    encrypted = EncryptedWebhookSecret(
        version=version,
        nonce=nonce,
        ciphertext=ciphertext,
    )

    return decrypt_webhook_secret(
        encrypted,
        organization_id=subscription.organization_id,
        subscription_id=subscription.id,
        encoded_encryption_key=(
            settings.WEBHOOK_SECRET_ENCRYPTION_KEY
        ),
    )


class AlertDeliveryWorkerError(RuntimeError):
    """Raised when a claimed delivery cannot be prepared safely."""


class AlertDeliveryWorker:
    def __init__(
        self,
        *,
        operational_reporter: WorkerOperationalReporter | None = None,
    ) -> None:
        self._stopping = False
        self._operational_reporter = operational_reporter

    def stop(self) -> None:
        self._stopping = True

    def validate_config(self) -> None:
        if POLL_SECONDS <= 0:
            raise AlertDeliveryWorkerError(
                "ALERT_DELIVERY_WORKER_POLL_SECONDS "
                "must be greater than zero."
            )

        if BATCH_SIZE < 1:
            raise AlertDeliveryWorkerError(
                "ALERT_DELIVERY_WORKER_BATCH_SIZE "
                "must be at least 1."
            )

        if MAX_ATTEMPTS < 1:
            raise AlertDeliveryWorkerError(
                "ALERT_DELIVERY_WORKER_MAX_ATTEMPTS "
                "must be at least 1."
            )

        if CLAIM_TIMEOUT_SECONDS < 1:
            raise AlertDeliveryWorkerError(
                "ALERT_DELIVERY_WORKER_CLAIM_TIMEOUT_SECONDS "
                "must be at least 1."
            )

        if RETRY_BASE_SECONDS < 1:
            raise AlertDeliveryWorkerError(
                "ALERT_DELIVERY_WORKER_RETRY_BASE_SECONDS "
                "must be at least 1."
            )

        if RETRY_MAX_SECONDS < RETRY_BASE_SECONDS:
            raise AlertDeliveryWorkerError(
                "ALERT_DELIVERY_WORKER_RETRY_MAX_SECONDS "
                "must be greater than or equal to retry base."
            )

        if HTTP_TIMEOUT_SECONDS <= 0:
            raise AlertDeliveryWorkerError(
                "ALERT_DELIVERY_WORKER_HTTP_TIMEOUT "
                "must be greater than zero."
            )

    def run(self) -> None:
        self.validate_config()

        logger.info(
            "worker_started worker_id=%s",
            WORKER_ID,
        )

        try:
            while not self._stopping:
                if self._operational_reporter is not None:
                    self._operational_reporter.cycle_started()

                try:
                    processed = self.run_once()

                except Exception as exc:
                    if self._operational_reporter is not None:
                        try:
                            self._operational_reporter.cycle_failed(
                                exc
                            )
                        except Exception:
                            logger.exception(
                                "operational_failure_report_failed"
                            )

                    raise

                else:
                    if self._operational_reporter is not None:
                        self._operational_reporter.cycle_succeeded()

                if processed == 0:
                    time.sleep(POLL_SECONDS)

        finally:
            logger.info(
                "worker_stopped worker_id=%s",
                WORKER_ID,
            )

    def run_once(self) -> int:
        self._finalize_exhausted()

        jobs = self._claim_batch()

        for job in jobs:
            try:
                self._process_job(job)
            except Exception:
                logger.exception(
                    (
                        "delivery_unhandled_error "
                        "delivery_id=%s "
                        "attempt=%s"
                    ),
                    job.delivery_id,
                    job.attempt_count,
                )

        return len(jobs)

    def _finalize_exhausted(self) -> int:
        db = SessionLocal()

        try:
            finalized = finalize_exhausted_alert_deliveries(
                db,
                limit=BATCH_SIZE,
                max_attempts=MAX_ATTEMPTS,
                claim_timeout_seconds=(
                    CLAIM_TIMEOUT_SECONDS
                ),
            )

            count = len(finalized)

            db.commit()

            if count:
                logger.warning(
                    (
                        "deliveries_exhausted "
                        "count=%s"
                    ),
                    count,
                )

            return count

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()

    def _claim_batch(
        self,
    ) -> tuple[ClaimedDeliveryJob, ...]:
        db = SessionLocal()

        try:
            claimed = claim_alert_deliveries(
                db,
                limit=BATCH_SIZE,
                max_attempts=MAX_ATTEMPTS,
                claim_timeout_seconds=(
                    CLAIM_TIMEOUT_SECONDS
                ),
            )

            jobs = tuple(
                ClaimedDeliveryJob(
                    delivery_id=delivery.id,
                    organization_id=delivery.organization_id,
                    destination_type=(
                        delivery.destination_type
                    ),
                    destination_id=delivery.destination_id,
                    delivery_key=delivery.delivery_key,
                    payload=dict(delivery.payload or {}),
                    attempt_count=int(
                        delivery.attempt_count or 0
                    ),
                )
                for delivery in claimed
            )

            db.commit()

            if jobs:
                logger.info(
                    (
                        "deliveries_claimed "
                        "count=%s"
                    ),
                    len(jobs),
                )

            return jobs

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()

    def _prepare_webhook_job(
        self,
        job: ClaimedDeliveryJob,
    ) -> WebhookDeliveryJob:
        if (
            job.destination_type
            != ALERT_DELIVERY_DESTINATION_WEBHOOK
        ):
            raise AlertDeliveryWorkerError(
                "Unsupported AlertDelivery destination type."
            )

        db = SessionLocal()

        try:
            delivery = (
                db.query(AlertDelivery)
                .filter(
                    AlertDelivery.id
                    == job.delivery_id
                )
                .one_or_none()
            )

            if delivery is None:
                raise AlertDeliveryRuntimeError(
                    "AlertDelivery does not exist."
                )

            if (
                delivery.status
                != ALERT_DELIVERY_STATUS_PROCESSING
            ):
                raise AlertDeliveryRuntimeError(
                    "AlertDelivery is not in processing state."
                )

            if (
                int(delivery.attempt_count or 0)
                != job.attempt_count
            ):
                raise AlertDeliveryRuntimeError(
                    "AlertDelivery attempt is no longer current."
                )

            subscription = (
                db.query(WebhookSubscription)
                .filter(
                    WebhookSubscription.id
                    == job.destination_id,
                    WebhookSubscription.organization_id
                    == job.organization_id,
                )
                .one_or_none()
            )

            if subscription is None:
                raise PermanentWebhookDeliveryError(
                    "Webhook destination no longer exists."
                )

            if subscription.is_active is not True:
                raise PermanentWebhookDeliveryError(
                    "Webhook destination is not active."
                )

            url = str(subscription.url).strip()

            if not url:
                raise PermanentWebhookDeliveryError(
                    "Webhook destination URL is empty."
                )

            secret_key = _resolve_webhook_secret(
                subscription
            )

            mark_delivery_attempt_started(
                db,
                delivery_id=job.delivery_id,
                expected_attempt_count=(
                    job.attempt_count
                ),
            )

            prepared = WebhookDeliveryJob(
                delivery_id=job.delivery_id,
                organization_id=job.organization_id,
                destination_id=job.destination_id,
                delivery_key=job.delivery_key,
                payload=dict(job.payload),
                attempt_count=job.attempt_count,
                url=url,
                secret_key=secret_key,
            )

            db.commit()

            return prepared

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()

    def _process_job(
        self,
        job: ClaimedDeliveryJob,
    ) -> None:
        try:
            prepared = self._prepare_webhook_job(
                job
            )

        except AlertDeliveryRuntimeError as exc:
            logger.warning(
                (
                    "delivery_claim_lost "
                    "delivery_id=%s "
                    "attempt=%s "
                    "reason=%s"
                ),
                job.delivery_id,
                job.attempt_count,
                str(exc),
            )
            return

        except PermanentWebhookDeliveryError as exc:
            self._persist_permanent_failure(
                job,
                error=str(exc),
            )
            return

        except Exception:
            # Do not persist arbitrary exception strings: they may include
            # tokens, URLs or downstream connection information.
            self._persist_transient_failure(
                job,
                error="Webhook preparation failed unexpectedly.",
            )
            return

        try:
            response = deliver_webhook(
                url=prepared.url,
                secret_key=prepared.secret_key,
                delivery_id=str(
                    prepared.delivery_id
                ),
                delivery_key=prepared.delivery_key,
                payload=prepared.payload,
                timeout_seconds=(
                    HTTP_TIMEOUT_SECONDS
                ),
            )

        except TransientWebhookDeliveryError as exc:
            self._persist_transient_failure(
                job,
                error=str(exc),
            )
            return

        except PermanentWebhookDeliveryError as exc:
            self._persist_permanent_failure(
                job,
                error=str(exc),
            )
            return

        except Exception:
            # Transport exception details are not safe tenant diagnostics.
            self._persist_transient_failure(
                job,
                error="Webhook transport failed unexpectedly.",
            )
            return

        if 200 <= response.status_code < 300:
            self._persist_delivered(job)

    def _persist_delivered(
        self,
        job: ClaimedDeliveryJob,
    ) -> None:
        db = SessionLocal()

        try:
            mark_delivery_delivered(
                db,
                delivery_id=job.delivery_id,
                expected_attempt_count=(
                    job.attempt_count
                ),
            )

            db.commit()

            logger.info(
                (
                    "delivery_delivered "
                    "delivery_id=%s "
                    "attempt=%s"
                ),
                job.delivery_id,
                job.attempt_count,
            )

        except AlertDeliveryRuntimeError as exc:
            db.rollback()

            logger.warning(
                (
                    "delivery_result_discarded "
                    "delivery_id=%s "
                    "attempt=%s "
                    "reason=%s"
                ),
                job.delivery_id,
                job.attempt_count,
                str(exc),
            )

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()

    def _persist_transient_failure(
        self,
        job: ClaimedDeliveryJob,
        *,
        error: str,
    ) -> None:
        db = SessionLocal()

        try:
            delivery = mark_delivery_retryable(
                db,
                delivery_id=job.delivery_id,
                expected_attempt_count=(
                    job.attempt_count
                ),
                error=error,
                max_attempts=MAX_ATTEMPTS,
                retry_base_seconds=(
                    RETRY_BASE_SECONDS
                ),
                retry_max_seconds=(
                    RETRY_MAX_SECONDS
                ),
            )

            db.commit()

            logger.warning(
                (
                    "delivery_transient_failure "
                    "delivery_id=%s "
                    "attempt=%s "
                    "status=%s "
                    "next_attempt_at=%s"
                ),
                job.delivery_id,
                job.attempt_count,
                delivery.status,
                delivery.next_attempt_at,
            )

        except AlertDeliveryRuntimeError as exc:
            db.rollback()

            logger.warning(
                (
                    "delivery_result_discarded "
                    "delivery_id=%s "
                    "attempt=%s "
                    "reason=%s"
                ),
                job.delivery_id,
                job.attempt_count,
                str(exc),
            )

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()

    def _persist_permanent_failure(
        self,
        job: ClaimedDeliveryJob,
        *,
        error: str,
    ) -> None:
        db = SessionLocal()

        try:
            mark_delivery_failed(
                db,
                delivery_id=job.delivery_id,
                expected_attempt_count=(
                    job.attempt_count
                ),
                error=error,
            )

            db.commit()

            logger.error(
                (
                    "delivery_permanently_failed "
                    "delivery_id=%s "
                    "attempt=%s "
                    "reason=%s"
                ),
                job.delivery_id,
                job.attempt_count,
                error,
            )

        except AlertDeliveryRuntimeError as exc:
            db.rollback()

            logger.warning(
                (
                    "delivery_result_discarded "
                    "delivery_id=%s "
                    "attempt=%s "
                    "reason=%s"
                ),
                job.delivery_id,
                job.attempt_count,
                str(exc),
            )

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()


def main() -> None:
    reporter = WorkerOperationalReporter(
        worker_kind=WORKER_KIND_ALERT_DELIVERY,
        worker_instance_id=WORKER_ID,
    )
    reporter.started()

    heartbeat = WorkerOperationalHeartbeat(
        reporter.heartbeat,
    )
    heartbeat.start()

    worker = AlertDeliveryWorker(
        operational_reporter=reporter,
    )

    def stop_worker(
        signum,
        frame,
    ) -> None:
        del signum
        del frame
        worker.stop()

    for sig in (
        signal.SIGINT,
        signal.SIGTERM,
    ):
        try:
            signal.signal(
                sig,
                stop_worker,
            )
        except (
            ValueError,
            OSError,
        ):
            pass

    try:
        worker.run()
        heartbeat.raise_if_failed()
    finally:
        heartbeat.stop()


if __name__ == "__main__":
    main()