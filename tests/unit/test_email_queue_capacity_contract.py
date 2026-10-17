from __future__ import annotations

import asyncio
from unittest.mock import Mock

from scripts import email_worker
from src.services import email_service


def test_queue_admission_never_trims_existing_entries() -> None:
    script = email_service.ENQUEUE_WITH_BACKPRESSURE_LUA
    assert "XLEN" in script
    assert "XADD" in script
    assert "MAXLEN" not in script.replace("EMAIL_STREAM_MAXLEN", "")
    assert "XTRIM" not in script


def test_retry_promotion_keeps_retry_when_stream_full() -> None:
    script = email_worker.PROMOTE_DUE_RETRY_LUA
    assert "XLEN" in script
    assert "return -1" in script
    assert script.index("XLEN") < script.index("XADD") < script.index("ZREM")


def test_terminal_ack_and_delete_are_atomic() -> None:
    script = email_worker.ACK_AND_DELETE_LUA
    assert "XACK" in script and "XDEL" in script
    assert script.index("XACK") < script.index("XDEL")
    worker = object.__new__(email_worker.EmailWorker)
    calls = []

    class FakeRedis:
        async def eval(self, *args):
            calls.append(args)
            return 1

    worker.redis = FakeRedis()
    asyncio.run(worker._ack_and_delete("100-1"))
    assert calls == [
        (email_worker.ACK_AND_DELETE_LUA, 1,
         email_worker.STREAM_KEY, email_worker.GROUP_NAME, "100-1")
    ]


def test_dlq_write_uses_atomic_bounded_admission() -> None:
    script = email_worker.DEAD_LETTER_WITH_BACKPRESSURE_LUA
    assert "XLEN" in script and "XADD" in script
    assert "XTRIM" not in script and "MAXLEN" not in script
    worker = object.__new__(email_worker.EmailWorker)
    calls = []

    class FakeRedis:
        async def eval(self, *args):
            calls.append(args)
            return "1-0"

    worker.redis = FakeRedis()
    asyncio.run(worker._dead_letter("1-0", {"id": "job"}, "reason"))
    assert len(calls) == 1
    script, key_count, key, capacity, *args = calls[0]
    assert script == email_worker.DEAD_LETTER_WITH_BACKPRESSURE_LUA
    assert key_count == 1 and key == email_worker.DEAD_LETTER_STREAM
    assert capacity == str(email_worker.DEAD_LETTER_MAXLEN)
    record = dict(zip(args[::2], args[1::2]))
    assert record["id"] == "job" and record["failed_message_id"] == "1-0"


def test_dlq_full_is_not_acknowledged() -> None:
    import pytest

    worker = object.__new__(email_worker.EmailWorker)

    class FakeRedis:
        async def eval(self, *args):
            return 0

    worker.redis = FakeRedis()
    with pytest.raises(RuntimeError, match="dead-letter queue is full"):
        asyncio.run(worker._dead_letter("1-0", {"id": "job"}, "reason"))
