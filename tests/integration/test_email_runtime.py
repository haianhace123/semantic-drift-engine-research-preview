from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import redis
import redis.asyncio as async_redis

from scripts import email_worker
from src.services import email_service


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def test_email_producer_and_worker_share_redis_stream_contract(
    monkeypatch,
) -> None:
    suffix = uuid.uuid4().hex
    stream_key = f"test:email:stream:{suffix}"
    group_name = f"test-email-workers-{suffix}"
    consumer_name = f"test-email-consumer-{suffix}"

    redis_url = email_service.settings.REDIS_URL

    monkeypatch.setattr(
        email_service.settings,
        "EMAIL_STREAM_KEY",
        stream_key,
    )
    monkeypatch.setattr(
        email_service.settings,
        "EMAIL_STREAM_MAXLEN",
        100,
    )
    monkeypatch.setattr(
        email_service.settings,
        "EMAIL_WORKER_MAX_ATTEMPTS",
        5,
    )

    monkeypatch.setattr(
        email_worker,
        "STREAM_KEY",
        stream_key,
    )
    monkeypatch.setattr(
        email_worker,
        "GROUP_NAME",
        group_name,
    )
    monkeypatch.setattr(
        email_worker,
        "CONSUMER_NAME",
        consumer_name,
    )
    monkeypatch.setattr(
        email_worker,
        "BLOCK_MS",
        100,
    )

    delivered: list[email_worker.EmailJob] = []

    async def fake_deliver(
        job: email_worker.EmailJob,
    ) -> None:
        delivered.append(job)

    async def run_worker() -> None:
        worker = object.__new__(
            email_worker.EmailWorker
        )
        worker.redis = async_redis.from_url(
            redis_url,
            decode_responses=True,
        )

        monkeypatch.setattr(
            worker,
            "_deliver",
            fake_deliver,
        )

        try:
            await worker._ensure_consumer_group()
            await worker._consume_once()

            pending = await worker.redis.xpending(
                stream_key,
                group_name,
            )

            assert pending["pending"] == 0
        finally:
            await worker.redis.aclose()

    job_id = email_service.enqueue_email(
        to=" runtime@example.com ",
        subject=" Runtime contract ",
        html="<p>runtime-contract</p>",
        text="runtime-contract",
        email_type="verification",
        idempotency_key="runtime-contract-key",
    )

    try:
        asyncio.run(run_worker())

        assert len(delivered) == 1

        job = delivered[0]

        assert job.job_id == job_id
        assert job.email_type == "verification"
        assert job.recipients == [
            "runtime@example.com"
        ]
        assert job.subject == "Runtime contract"
        assert job.html == (
            "<p>runtime-contract</p>"
        )
        assert job.text == "runtime-contract"
        assert (
            job.idempotency_key
            == "runtime-contract-key"
        )
        assert job.attempt == 0
        assert job.max_attempts == 5

    finally:
        client = redis.from_url(
            redis_url,
            decode_responses=True,
        )
        try:
            client.delete(stream_key)
        finally:
            client.close()