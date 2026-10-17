"""Production transactional email worker backed by Redis Streams and Resend."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time

from dataclasses import dataclass
from typing import Any

import httpx
import redis.asyncio as redis

from redis.exceptions import ResponseError

from src.models.worker_operational_state import (
    WORKER_KIND_EMAIL,
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


SERVICE_NAME = "semantic-drift-email-worker"

WORKER_ID = create_worker_instance_id()

REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://localhost:6379/0",
)

STREAM_KEY = os.getenv(
    "EMAIL_STREAM_KEY",
    "sde:email:stream",
)

GROUP_NAME = os.getenv(
    "EMAIL_STREAM_GROUP",
    "sde-email-workers",
)

CONSUMER_NAME = os.getenv(
    "EMAIL_WORKER_CONSUMER",
    WORKER_ID,
)

DEAD_LETTER_STREAM = os.getenv(
    "EMAIL_DEAD_LETTER_STREAM",
    "sde:email:dead",
)

RETRY_ZSET = os.getenv(
    "EMAIL_RETRY_ZSET",
    "sde:email:retry",
)

# A retry must never leave the sorted set before Redis has accepted the
# replacement stream entry. The whole transfer is one Redis operation;
# competing workers cannot promote the same retry twice.
PROMOTE_DUE_RETRY_LUA = """
local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not score or tonumber(score) > tonumber(ARGV[2]) then
    return 0
end
if redis.call('XLEN', KEYS[2]) >= tonumber(ARGV[3]) then
    return -1
end
redis.call('XADD', KEYS[2], '*', unpack(ARGV, 4))
redis.call('ZREM', KEYS[1], ARGV[1])
return 1
"""

# Acknowledging and removing an entry must not leave a permanent
# acknowledged entry that consumes queue capacity after a crash.
ACK_AND_DELETE_LUA = """
local acknowledged = redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
if acknowledged == 1 then
    redis.call('XDEL', KEYS[1], ARGV[2])
end
return acknowledged
"""

# Dead-letter admission is bounded without evicting audit evidence.
# When full the source message remains pending for operator recovery.
DEAD_LETTER_WITH_BACKPRESSURE_LUA = """
if redis.call('XLEN', KEYS[1]) >= tonumber(ARGV[1]) then
    return 0
end
return redis.call('XADD', KEYS[1], '*', unpack(ARGV, 2))
"""

STREAM_MAXLEN = int(
    os.getenv(
        "EMAIL_STREAM_MAXLEN",
        "10000",
    )
)

DEAD_LETTER_MAXLEN = int(
    os.getenv(
        "EMAIL_DEAD_LETTER_MAXLEN",
        "10000",
    )
)

RESEND_API_KEY = os.getenv(
    "RESEND_API_KEY",
    "",
)

EMAIL_FROM = os.getenv(
    "EMAIL_FROM",
    "",
)

EMAIL_REPLY_TO = os.getenv(
    "EMAIL_REPLY_TO",
    "",
)

CLAIM_IDLE_MS = int(
    os.getenv(
        "EMAIL_WORKER_CLAIM_IDLE_MS",
        "120000",
    )
)

BLOCK_MS = int(
    os.getenv(
        "EMAIL_WORKER_BLOCK_MS",
        "5000",
    )
)

BATCH_SIZE = int(
    os.getenv(
        "EMAIL_WORKER_BATCH_SIZE",
        "10",
    )
)

MAX_ATTEMPTS = int(
    os.getenv(
        "EMAIL_WORKER_MAX_ATTEMPTS",
        "5",
    )
)

RETRY_BASE_SECONDS = int(
    os.getenv(
        "EMAIL_WORKER_RETRY_BASE_SECONDS",
        "5",
    )
)

RETRY_MAX_SECONDS = int(
    os.getenv(
        "EMAIL_WORKER_RETRY_MAX_SECONDS",
        "300",
    )
)

HTTP_TIMEOUT = float(
    os.getenv(
        "EMAIL_WORKER_HTTP_TIMEOUT",
        "20",
    )
)


logger = logging.getLogger(
    SERVICE_NAME
)

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

    logger.addHandler(
        handler
    )

logger.setLevel(
    os.getenv(
        "LOG_LEVEL",
        "INFO",
    ).upper()
)

logger.propagate = False


@dataclass(slots=True)
class EmailJob:
    job_id: str
    email_type: str
    recipients: list[str]
    subject: str
    html: str
    text: str | None
    idempotency_key: str
    attempt: int
    max_attempts: int

    @classmethod
    def from_fields(
        cls,
        fields: dict[str, Any],
    ) -> "EmailJob":
        raw_to = fields.get(
            "to",
            "[]",
        )

        recipients = (
            json.loads(raw_to)
            if isinstance(
                raw_to,
                str,
            )
            else raw_to
        )

        if isinstance(
            recipients,
            str,
        ):
            recipients = [
                recipients
            ]

        if (
            not isinstance(
                recipients,
                list,
            )
            or not recipients
        ):
            raise ValueError(
                "Email job requires at least one recipient"
            )

        normalized_recipients = [
            str(item).strip()
            for item in recipients
            if str(item).strip()
        ]

        if not normalized_recipients:
            raise ValueError(
                "Email job requires at least one valid recipient"
            )

        subject = str(
            fields.get(
                "subject",
                "",
            )
        ).strip()

        html = str(
            fields.get(
                "html",
                "",
            )
        )

        if not subject:
            raise ValueError(
                "Email job requires a subject"
            )

        if not html:
            raise ValueError(
                "Email job requires an HTML body"
            )

        job_id = str(
            fields.get("id")
            or uuid.uuid4().hex
        )

        idempotency_key = str(
            fields.get(
                "idempotency_key",
            )
            or job_id
        ).strip()

        if not idempotency_key:
            raise ValueError(
                "Email job requires an idempotency key"
            )

        if len(
            idempotency_key
        ) > 256:
            raise ValueError(
                "Email idempotency key exceeds 256 characters"
            )

        raw_max_attempts = int(
            fields.get(
                "max_attempts",
            )
            or MAX_ATTEMPTS
        )

        return cls(
            job_id=job_id,
            email_type=str(
                fields.get(
                    "type",
                )
                or "transactional"
            ),
            recipients=normalized_recipients,
            subject=subject,
            html=html,
            text=(
                str(
                    fields.get(
                        "text"
                    )
                )
                if fields.get(
                    "text"
                )
                else None
            ),
            idempotency_key=idempotency_key,
            attempt=max(
                0,
                int(
                    fields.get(
                        "attempt"
                    )
                    or 0
                ),
            ),
            max_attempts=max(
                1,
                min(
                    raw_max_attempts,
                    MAX_ATTEMPTS,
                ),
            ),
        )


def retry_delay(
    attempt: int,
) -> int:
    return min(
        RETRY_BASE_SECONDS
        * (
            2
            ** max(
                0,
                attempt - 1,
            )
        ),
        RETRY_MAX_SECONDS,
    )


class EmailWorker:
    """Reliable email worker using Redis consumer-group acknowledgements."""

    def __init__(
        self,
        *,
        operational_reporter: WorkerOperationalReporter | None = None,
    ) -> None:
        self._operational_reporter = operational_reporter

        self.redis = redis.from_url(
            REDIS_URL,
            decode_responses=True,
            health_check_interval=30,
            socket_connect_timeout=5,
            socket_timeout=max(
                10,
                BLOCK_MS / 1000 + 5,
            ),
        )

        self.stop_event = (
            asyncio.Event()
        )

        self.http = (
            httpx.AsyncClient(
                timeout=HTTP_TIMEOUT,
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                ),
            )
        )

    async def _report_operational(
        self,
        operation,
        *args,
    ) -> None:
        if self._operational_reporter is None:
            return

        await asyncio.to_thread(
            operation,
            *args,
        )

    async def start(
        self,
    ) -> None:
        self._validate_config()

        await self._ensure_consumer_group()
        await self._recover_pending()

        logger.info(
            "worker_started worker_id=%s",
            WORKER_ID,
        )

        try:
            while not self.stop_event.is_set():
                if self._operational_reporter is not None:
                    await self._report_operational(
                        self._operational_reporter.cycle_started
                    )

                try:
                    await self._promote_due_retries()
                    await self._recover_pending_once()
                    await self._consume_once()

                except Exception as exc:
                    if self._operational_reporter is not None:
                        try:
                            await self._report_operational(
                                self._operational_reporter.cycle_failed,
                                exc,
                            )
                        except Exception:
                            logger.exception(
                                "operational_failure_report_failed"
                            )

                    raise

                else:
                    if self._operational_reporter is not None:
                        await self._report_operational(
                            self._operational_reporter.cycle_succeeded
                        )

        finally:
            await self.http.aclose()
            await self.redis.aclose()

            logger.info(
                "worker_stopped worker_id=%s",
                WORKER_ID,
            )

    def _validate_config(
        self,
    ) -> None:
        missing: list[str] = []

        if not RESEND_API_KEY:
            missing.append(
                "RESEND_API_KEY"
            )

        if not EMAIL_FROM:
            missing.append(
                "EMAIL_FROM"
            )

        if missing:
            raise RuntimeError(
                "Missing required configuration: "
                + ", ".join(
                    missing
                )
            )

        if STREAM_MAXLEN < 1 or DEAD_LETTER_MAXLEN < 1:
            raise RuntimeError("Email stream capacities must be positive")

    async def _ensure_consumer_group(
        self,
    ) -> None:
        try:
            await self.redis.xgroup_create(
                STREAM_KEY,
                GROUP_NAME,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as exc:
            if (
                "BUSYGROUP"
                not in str(exc)
            ):
                raise

    async def _recover_pending(
        self,
    ) -> None:
        """Claim jobs abandoned by dead workers before consuming new traffic."""
        while True:
            entries = (
                await self.redis.xautoclaim(
                    STREAM_KEY,
                    GROUP_NAME,
                    CONSUMER_NAME,
                    min_idle_time=CLAIM_IDLE_MS,
                    start_id="0-0",
                    count=BATCH_SIZE,
                )
            )

            claimed = (
                entries[1]
                if len(entries) > 1
                else []
            )

            if not claimed:
                break

            await self._process_entries(
                claimed
            )

            if (
                len(claimed)
                < BATCH_SIZE
            ):
                break

    async def _recover_pending_once(
        self,
    ) -> None:
        await self._recover_pending()

    async def _consume_once(
        self,
    ) -> None:
        result = (
            await self.redis.xreadgroup(
                GROUP_NAME,
                CONSUMER_NAME,
                {
                    STREAM_KEY: ">"
                },
                count=BATCH_SIZE,
                block=BLOCK_MS,
            )
        )

        if not result:
            return

        for _, entries in result:
            await self._process_entries(
                entries
            )

    async def _process_entries(
        self,
        entries: list[Any],
    ) -> None:
        for (
            message_id,
            fields,
        ) in entries:
            try:
                await self._process_entry(
                    message_id,
                    fields,
                )
            except Exception:
                logger.exception(
                    (
                        "unhandled_job_error "
                        "message_id=%s"
                    ),
                    message_id,
                )

    async def _ack_and_delete(self, message_id: str) -> None:
        """Remove a terminal job only after acknowledging its PEL entry."""
        await self.redis.eval(
            ACK_AND_DELETE_LUA,
            1,
            STREAM_KEY,
            GROUP_NAME,
            message_id,
        )

    async def _process_entry(
        self,
        message_id: str,
        fields: dict[str, Any],
    ) -> None:
        try:
            job = EmailJob.from_fields(
                fields
            )

            await self._deliver(
                job
            )

            await self._ack_and_delete(message_id)

            logger.info(
                (
                    "email_delivered "
                    "job_id=%s "
                    "type=%s "
                    "attempt=%s"
                ),
                job.job_id,
                job.email_type,
                job.attempt,
            )

        except PermanentEmailError as exc:
            await self._dead_letter(
                message_id,
                fields,
                str(exc),
            )

            await self._ack_and_delete(message_id)

            logger.error(
                (
                    "email_permanently_failed "
                    "job_id=%s "
                    "reason=%s"
                ),
                fields.get("id"),
                str(exc),
            )

        except Exception as exc:
            attempt = (
                int(
                    fields.get(
                        "attempt"
                    )
                    or 0
                )
                + 1
            )

            max_attempts = max(
                1,
                min(
                    int(
                        fields.get(
                            "max_attempts"
                        )
                        or MAX_ATTEMPTS
                    ),
                    MAX_ATTEMPTS,
                ),
            )

            if (
                attempt
                >= max_attempts
            ):
                await self._dead_letter(
                    message_id,
                    fields,
                    str(exc),
                )

                await self._ack_and_delete(message_id)

                logger.error(
                    (
                        "email_dead_lettered "
                        "job_id=%s"
                    ),
                    fields.get("id"),
                )

                return

            delay = retry_delay(
                attempt
            )

            await self._schedule_retry(
                fields,
                attempt,
                delay,
            )

            await self._ack_and_delete(message_id)

            logger.warning(
                (
                    "email_retry_scheduled "
                    "job_id=%s "
                    "attempt=%s "
                    "delay=%ss"
                ),
                fields.get("id"),
                attempt,
                delay,
            )

    async def _deliver(
        self,
        job: EmailJob,
    ) -> None:
        payload: dict[str, Any] = {
            "from": EMAIL_FROM,
            "to": job.recipients,
            "subject": job.subject,
            "html": job.html,
        }

        if job.text:
            payload["text"] = (
                job.text
            )

        if EMAIL_REPLY_TO:
            payload["reply_to"] = (
                EMAIL_REPLY_TO
            )

        try:
            response = (
                await self.http.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": (
                            f"Bearer {RESEND_API_KEY}"
                        ),
                        "Content-Type": (
                            "application/json"
                        ),
                        "User-Agent": (
                            "semantic-drift-email-worker/1.0"
                        ),
                        "Idempotency-Key": (
                            job.idempotency_key
                        ),
                    },
                    json=payload,
                )
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
        ) as exc:
            raise TransientEmailError(
                str(exc)
            ) from exc

        if (
            200
            <= response.status_code
            < 300
        ):
            return

        if response.status_code in {
            408,
            409,
            429,
            500,
            502,
            503,
            504,
        }:
            raise TransientEmailError(
                (
                    "Resend returned "
                    f"HTTP {response.status_code}"
                )
            )

        raise PermanentEmailError(
            (
                "Resend returned "
                f"HTTP {response.status_code}"
            )
        )

    async def _schedule_retry(
        self,
        fields: dict[str, Any],
        attempt: int,
        delay: int,
    ) -> None:
        """Persist a retry request in a durable sorted set."""
        retry_fields = dict(
            fields
        )

        retry_fields[
            "attempt"
        ] = str(
            attempt
        )

        retry_payload = (
            json.dumps(
                retry_fields,
                separators=(
                    ",",
                    ":",
                ),
            )
        )

        retry_at = (
            time.time()
            + delay
        )

        await self.redis.zadd(
            RETRY_ZSET,
            {
                retry_payload: retry_at
            },
        )

    async def _promote_due_retries(
        self,
    ) -> None:
        """Move due retry jobs back into the main stream."""
        now = time.time()

        payloads = (
            await self.redis.zrangebyscore(
                RETRY_ZSET,
                0,
                now,
                start=0,
                num=BATCH_SIZE,
            )
        )

        if not payloads:
            return

        for payload in payloads:
            fields = json.loads(payload)
            if (
                not isinstance(fields, dict)
                or not fields
                or any(
                    not isinstance(key, str)
                    or not isinstance(value, str)
                    for key, value in fields.items()
                )
            ):
                raise ValueError(
                    "Malformed email retry payload."
                )

            field_args = [
                field
                for key, value in fields.items()
                for field in (key, value)
            ]

            # ZSCORE, XADD, and ZREM execute atomically inside Redis.
            # On a stream write failure, the retry remains scheduled.
            # The script rechecks the due score so parallel workers
            # cannot enqueue the same job twice.
            await self.redis.eval(
                PROMOTE_DUE_RETRY_LUA,
                2,
                RETRY_ZSET,
                STREAM_KEY,
                payload,
                str(now),
                str(STREAM_MAXLEN),
                *field_args,
            )

    async def _dead_letter(
        self,
        message_id: str,
        fields: dict[str, Any],
        reason: str,
    ) -> None:
        record = dict(
            fields
        )

        record[
            "failed_message_id"
        ] = message_id

        record[
            "failure_reason"
        ] = reason[:500]

        record[
            "failed_at"
        ] = str(
            time.time()
        )

        field_args = [
            field
            for key, value in record.items()
            for field in (key, value)
        ]
        result = await self.redis.eval(
            DEAD_LETTER_WITH_BACKPRESSURE_LUA,
            1,
            DEAD_LETTER_STREAM,
            str(DEAD_LETTER_MAXLEN),
            *field_args,
        )
        if result == 0:
            # The caller does not ACK the original stream entry.
            raise RuntimeError("Email dead-letter queue is full")

    def stop(
        self,
    ) -> None:
        self.stop_event.set()


class TransientEmailError(
    Exception
):
    """Raised for failures that should be retried."""


class PermanentEmailError(
    Exception
):
    """Raised for failures that should not be retried."""


async def main() -> None:
    reporter = WorkerOperationalReporter(
        worker_kind=WORKER_KIND_EMAIL,
        worker_instance_id=WORKER_ID,
    )

    await asyncio.to_thread(
        reporter.started
    )

    heartbeat = WorkerOperationalHeartbeat(
        reporter.heartbeat,
    )
    await asyncio.to_thread(
        heartbeat.start
    )

    worker = EmailWorker(
        operational_reporter=reporter,
    )

    loop = (
        asyncio.get_running_loop()
    )

    for sig in (
        signal.SIGINT,
        signal.SIGTERM,
    ):
        try:
            loop.add_signal_handler(
                sig,
                worker.stop,
            )
        except (
            NotImplementedError,
            RuntimeError,
        ):
            pass

    try:
        await worker.start()
        await asyncio.to_thread(
            heartbeat.raise_if_failed
        )
    finally:
        await asyncio.to_thread(
            heartbeat.stop
        )


if __name__ == "__main__":
    asyncio.run(
        main()
    )