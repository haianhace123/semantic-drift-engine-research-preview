from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import redis
import redis.asyncio as async_redis

from scripts import email_worker
from src.services import email_service


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _fields() -> dict[str, str]:
    return {
        "id": uuid.uuid4().hex,
        "type": "verification",
        "to": '["test@example.com"]',
        "subject": "Verify",
        "html": "<p>verify</p>",
        "idempotency_key": uuid.uuid4().hex,
        "attempt": "0",
        "max_attempts": "5",
    }


def test_queue_rejects_overflow_without_trimming_pending_and_releases_slot(
    monkeypatch,
) -> None:
    suffix = uuid.uuid4().hex
    stream = f"test:email:capacity:{suffix}"
    group = f"test:email:capacity:group:{suffix}"
    monkeypatch.setattr(email_service.settings, "EMAIL_STREAM_KEY", stream)
    monkeypatch.setattr(email_service.settings, "EMAIL_STREAM_MAXLEN", 1)
    monkeypatch.setattr(email_worker, "STREAM_KEY", stream)
    monkeypatch.setattr(email_worker, "GROUP_NAME", group)
    monkeypatch.setattr(email_worker, "CONSUMER_NAME", f"test:{suffix}")
    client = redis.from_url(email_service.settings.REDIS_URL, decode_responses=True)

    try:
        original_id = email_service.enqueue_email(
            to="user@example.com", subject="Subject", html="<p>first</p>"
        )
        client.xgroup_create(stream, group, id="0-0")
        pending = client.xreadgroup(group, "claim-owner", {stream: ">"}, count=1)
        assert len(pending[0][1]) == 1
        original_message_id = pending[0][1][0][0]
        assert pending[0][1][0][1]["id"] == original_id

        with pytest.raises(email_service.EmailQueueFullError):
            email_service.enqueue_email(
                to="user@example.com", subject="Subject", html="<p>second</p>"
            )

        assert client.xlen(stream) == 1
        assert client.xpending(stream, group)["pending"] == 1
        assert client.xrange(stream)[0][1]["id"] == original_id

        async def finish() -> None:
            worker = object.__new__(email_worker.EmailWorker)
            worker.redis = async_redis.from_url(
                email_service.settings.REDIS_URL,
                decode_responses=True,
            )
            try:
                await worker._ack_and_delete(original_message_id)
            finally:
                await worker.redis.aclose()

        asyncio.run(finish())
        assert client.xlen(stream) == 0
        assert client.xpending(stream, group)["pending"] == 0

        subsequent_id = email_service.enqueue_email(
            to="user@example.com", subject="Subject", html="<p>second</p>"
        )
        assert client.xrange(stream)[0][1]["id"] == subsequent_id
    finally:
        client.delete(stream)
        client.close()


def test_retry_promotion_waits_for_capacity_without_losing_scheduled_job(
    monkeypatch,
) -> None:
    suffix = uuid.uuid4().hex
    stream = f"test:email:capacity:retry-stream:{suffix}"
    retry = f"test:email:capacity:retry:{suffix}"
    monkeypatch.setattr(email_worker, "STREAM_KEY", stream)
    monkeypatch.setattr(email_worker, "RETRY_ZSET", retry)
    monkeypatch.setattr(email_worker, "STREAM_MAXLEN", 1)
    payload = json.dumps(_fields())

    async def run() -> None:
        client = async_redis.from_url(
            email_service.settings.REDIS_URL, decode_responses=True
        )
        worker = object.__new__(email_worker.EmailWorker)
        worker.redis = client
        try:
            await client.xadd(stream, _fields())
            await client.zadd(retry, {payload: time.time() - 60})
            await worker._promote_due_retries()
            assert await client.xlen(stream) == 1
            assert await client.zscore(retry, payload) is not None

            await client.delete(stream)
            await worker._promote_due_retries()
            assert await client.zscore(retry, payload) is None
            assert await client.xlen(stream) == 1
            assert (await client.xrange(stream))[0][1] == json.loads(payload)
        finally:
            await client.delete(stream, retry)
            await client.aclose()

    asyncio.run(run())


def test_concurrent_producers_respect_capacity(monkeypatch) -> None:
    suffix = uuid.uuid4().hex
    stream = f"test:email:capacity:parallel:{suffix}"
    monkeypatch.setattr(email_service.settings, "EMAIL_STREAM_KEY", stream)
    monkeypatch.setattr(email_service.settings, "EMAIL_STREAM_MAXLEN", 2)
    client = redis.from_url(email_service.settings.REDIS_URL, decode_responses=True)

    def enqueue_one(_: int) -> str:
        try:
            return email_service.enqueue_email(
                to="user@example.com", subject="Subject", html="<p>body</p>"
            )
        except email_service.EmailQueueFullError:
            return "full"

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(enqueue_one, range(8)))
        assert sum(result != "full" for result in results) == 2
        assert client.xlen(stream) == 2
    finally:
        client.delete(stream)
        client.close()


def test_dead_letter_refuses_overflow_without_eviction(monkeypatch) -> None:
    suffix = uuid.uuid4().hex
    dead = f"test:email:capacity:dead:{suffix}"
    monkeypatch.setattr(email_worker, "DEAD_LETTER_STREAM", dead)
    monkeypatch.setattr(email_worker, "DEAD_LETTER_MAXLEN", 1)

    async def run() -> None:
        client = async_redis.from_url(
            email_service.settings.REDIS_URL, decode_responses=True
        )
        worker = object.__new__(email_worker.EmailWorker)
        worker.redis = client
        try:
            await worker._dead_letter("1-0", _fields(), "bad payload")
            initial = await client.xrange(dead)
            with pytest.raises(RuntimeError, match="dead-letter queue is full"):
                await worker._dead_letter("2-0", _fields(), "provider rejection")
            assert await client.xlen(dead) == 1
            assert await client.xrange(dead) == initial
        finally:
            await client.delete(dead)
            await client.aclose()

    asyncio.run(run())


def test_full_dead_letter_does_not_ack_source_message(monkeypatch) -> None:
    suffix = uuid.uuid4().hex
    stream = f"test:email:capacity:source:{suffix}"
    group = f"test:email:capacity:source-group:{suffix}"
    dead = f"test:email:capacity:dead-full:{suffix}"
    monkeypatch.setattr(email_worker, "STREAM_KEY", stream)
    monkeypatch.setattr(email_worker, "GROUP_NAME", group)
    monkeypatch.setattr(email_worker, "DEAD_LETTER_STREAM", dead)
    monkeypatch.setattr(email_worker, "DEAD_LETTER_MAXLEN", 1)

    async def run() -> None:
        client = async_redis.from_url(
            email_service.settings.REDIS_URL, decode_responses=True
        )
        worker = object.__new__(email_worker.EmailWorker)
        worker.redis = client

        async def reject(_job: email_worker.EmailJob) -> None:
            raise email_worker.PermanentEmailError("provider rejected")

        worker._deliver = reject
        try:
            await client.xadd(dead, {"id": "historical-dead-letter"})
            await client.xadd(stream, _fields())
            await client.xgroup_create(stream, group, id="0-0")
            messages = await client.xreadgroup(group, "test-consumer", {stream: ">"})
            message_id, fields = messages[0][1][0]
            with pytest.raises(RuntimeError, match="dead-letter queue is full"):
                await worker._process_entry(message_id, fields)
            assert (await client.xpending(stream, group))["pending"] == 1
            assert await client.xlen(stream) == 1
            assert await client.xlen(dead) == 1
        finally:
            await client.delete(stream, dead)
            await client.aclose()

    asyncio.run(run())
