import json

import pytest

from src.services import email_service


class FakeRedis:
    def __init__(self) -> None:
        self.calls = []
        self.closed = False

    def eval(self, script, key_count, stream, capacity, *args):
        self.calls.append(
            {
                "script": script,
                "key_count": key_count,
                "stream": stream,
                "capacity": capacity,
                "fields": dict(zip(args[::2], args[1::2])),
            }
        )
        return "1-0"

    def close(self) -> None:
        self.closed = True


def test_enqueue_email_publishes_worker_compatible_stream_job(
    monkeypatch,
):
    client = FakeRedis()

    monkeypatch.setattr(
        email_service,
        "redis_client",
        lambda: client,
    )

    monkeypatch.setattr(
        email_service.settings,
        "EMAIL_STREAM_KEY",
        "test:email:stream",
    )

    monkeypatch.setattr(
        email_service.settings,
        "EMAIL_STREAM_MAXLEN",
        250,
    )

    monkeypatch.setattr(
        email_service.settings,
        "EMAIL_WORKER_MAX_ATTEMPTS",
        5,
    )

    job_id = email_service.enqueue_email(
        to=" user@example.com ",
        subject=" Verify your account ",
        html="<p>123456</p>",
        text="123456",
        email_type="verification",
        idempotency_key="verify-user-123",
    )

    assert len(job_id) == 32
    assert len(client.calls) == 1

    call = client.calls[0]
    fields = call["fields"]

    assert (
        call["stream"]
        == "test:email:stream"
    )

    assert call["script"] == email_service.ENQUEUE_WITH_BACKPRESSURE_LUA
    assert call["key_count"] == 1
    assert call["capacity"] == "250"

    assert fields["id"] == job_id

    assert (
        fields["type"]
        == "verification"
    )

    assert (
        json.loads(
            fields["to"]
        )
        == ["user@example.com"]
    )

    assert (
        fields["subject"]
        == "Verify your account"
    )

    assert (
        fields["html"]
        == "<p>123456</p>"
    )

    assert fields["text"] == "123456"

    assert (
        fields["idempotency_key"]
        == "verify-user-123"
    )

    assert fields["attempt"] == "0"
    assert fields["max_attempts"] == "5"

    assert client.closed is True


def test_enqueue_email_rejects_invalid_payload(
    monkeypatch,
):
    client = FakeRedis()

    monkeypatch.setattr(
        email_service,
        "redis_client",
        lambda: client,
    )

    with pytest.raises(
        ValueError,
        match="recipient",
    ):
        email_service.enqueue_email(
            to="   ",
            subject="Subject",
            html="<p>body</p>",
        )

    with pytest.raises(
        ValueError,
        match="subject",
    ):
        email_service.enqueue_email(
            to="user@example.com",
            subject="   ",
            html="<p>body</p>",
        )

    with pytest.raises(
        ValueError,
        match="HTML",
    ):
        email_service.enqueue_email(
            to="user@example.com",
            subject="Subject",
            html="",
        )

    assert client.calls == []
    assert client.closed is False

def test_enqueue_fails_closed_when_stream_is_full(monkeypatch):
    client = FakeRedis()
    client.eval = lambda *args: 0
    monkeypatch.setattr(email_service, "redis_client", lambda: client)
    with pytest.raises(email_service.EmailQueueFullError, match="full"):
        email_service.enqueue_email(
            to="user@example.com", subject="Test", html="<p>test</p>"
        )
    assert client.closed is True


def test_enqueue_rejects_invalid_capacity(monkeypatch):
    monkeypatch.setattr(email_service.settings, "EMAIL_STREAM_MAXLEN", 0)
    with pytest.raises(ValueError, match="must be positive"):
        email_service.enqueue_email(
            to="user@example.com", subject="Test", html="<p>test</p>"
        )
