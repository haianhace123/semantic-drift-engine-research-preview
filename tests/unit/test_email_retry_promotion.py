from __future__ import annotations

import asyncio
import json
from unittest.mock import Mock

import pytest

from scripts import email_worker


class FakeRedis:
    def __init__(self, payloads: list[str]) -> None:
        self.payloads = payloads
        self.eval_calls: list[tuple] = []
        self.removed = False

    async def zrangebyscore(self, *args, **kwargs):
        return self.payloads

    async def eval(self, *args):
        self.eval_calls.append(args)
        return 1


def make_worker(redis_client):
    worker = object.__new__(email_worker.EmailWorker)
    worker.redis = redis_client
    return worker


def test_retry_promotion_uses_atomic_redis_script(monkeypatch):
    monkeypatch.setattr(email_worker, "RETRY_ZSET", "retry:unit")
    monkeypatch.setattr(email_worker, "STREAM_KEY", "stream:unit")
    fields = {
        "id": "job-1",
        "to": '["user@example.com"]',
        "attempt": "1",
        "idempotency_key": "stable-key",
    }
    payload = json.dumps(fields)
    redis_client = FakeRedis([payload])

    asyncio.run(make_worker(redis_client)._promote_due_retries())

    assert len(redis_client.eval_calls) == 1
    script, key_count, retry_key, stream_key, raw, timestamp, capacity, *args = (
        redis_client.eval_calls[0]
    )
    assert script == email_worker.PROMOTE_DUE_RETRY_LUA
    assert key_count == 2
    assert retry_key == "retry:unit"
    assert stream_key == "stream:unit"
    assert raw == payload
    assert float(timestamp) > 0
    assert int(capacity) == email_worker.STREAM_MAXLEN
    assert dict(zip(args[::2], args[1::2])) == fields
    assert not hasattr(redis_client, "zrem")
    assert not hasattr(redis_client, "xadd")


@pytest.mark.parametrize("payload", ["not-json", "[]", '{"id": 123}', "{}"])
def test_invalid_retry_payload_is_not_removed(payload):
    redis_client = FakeRedis([payload])

    with pytest.raises((ValueError, json.JSONDecodeError)):
        asyncio.run(make_worker(redis_client)._promote_due_retries())

    assert redis_client.eval_calls == []
