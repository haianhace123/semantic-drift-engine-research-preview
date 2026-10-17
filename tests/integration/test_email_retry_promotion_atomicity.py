from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import pytest
import redis.asyncio as redis_async
from redis.exceptions import ResponseError

from scripts import email_worker

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _job_fields() -> dict[str, str]:
    return {
        "id": uuid.uuid4().hex,
        "type": "verification",
        "to": '["test@example.com"]',
        "subject": "Verify",
        "html": "<p>code</p>",
        "idempotency_key": uuid.uuid4().hex,
        "attempt": "1",
        "max_attempts": "5",
    }


def test_retry_promotion_failure_keeps_job_and_recovers(monkeypatch) -> None:
    suffix = uuid.uuid4().hex
    retry_key = f"test:email:retry:{suffix}"
    stream_key = f"test:email:stream:{suffix}"
    monkeypatch.setattr(email_worker, "RETRY_ZSET", retry_key)
    monkeypatch.setattr(email_worker, "STREAM_KEY", stream_key)

    async def run() -> None:
        client = redis_async.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/0"),
            decode_responses=True,
        )
        worker = object.__new__(email_worker.EmailWorker)
        worker.redis = client
        fields = _job_fields()
        payload = json.dumps(fields)
        try:
            await client.zadd(retry_key, {payload: time.time() - 60})
            # Force XADD to error after the retry is found.
            await client.set(stream_key, "wrong-type")
            with pytest.raises(ResponseError):
                await worker._promote_due_retries()

            assert await client.zcard(retry_key) == 1
            assert await client.zscore(retry_key, payload) is not None
            assert await client.get(stream_key) == "wrong-type"

            await client.delete(stream_key)
            await worker._promote_due_retries()

            assert await client.zcard(retry_key) == 0
            entries = await client.xrange(stream_key)
            assert len(entries) == 1
            assert entries[0][1] == fields
        finally:
            await client.delete(retry_key, stream_key)
            await client.aclose()

    asyncio.run(run())


def test_concurrent_workers_promote_once_and_respect_due_time(monkeypatch) -> None:
    suffix = uuid.uuid4().hex
    retry_key = f"test:email:retry:{suffix}"
    stream_key = f"test:email:stream:{suffix}"
    monkeypatch.setattr(email_worker, "RETRY_ZSET", retry_key)
    monkeypatch.setattr(email_worker, "STREAM_KEY", stream_key)

    async def run() -> None:
        clients = [
            redis_async.from_url(
                os.getenv("REDIS_URL", "redis://redis:6379/0"),
                decode_responses=True,
            )
            for _ in range(4)
        ]
        fields = _job_fields()
        future_fields = _job_fields()
        due_payload = json.dumps(fields)
        future_payload = json.dumps(future_fields)
        try:
            await clients[0].zadd(
                retry_key,
                {
                    due_payload: time.time() - 60,
                    future_payload: time.time() + 3600,
                },
            )
            workers = []
            for client in clients:
                worker = object.__new__(email_worker.EmailWorker)
                worker.redis = client
                workers.append(worker)
            await asyncio.gather(
                *(worker._promote_due_retries() for worker in workers)
            )

            entries = await clients[0].xrange(stream_key)
            assert len(entries) == 1
            assert entries[0][1] == fields
            assert await clients[0].zscore(retry_key, due_payload) is None
            assert await clients[0].zscore(retry_key, future_payload) is not None
        finally:
            await clients[0].delete(retry_key, stream_key)
            await asyncio.gather(*(client.aclose() for client in clients))

    asyncio.run(run())
