"""
Transactional email queue for Semantic Drift Engine.

The API process only publishes durable email jobs to Redis Streams.
Delivery is owned by ``scripts/email_worker.py`` so provider calls,
retries, acknowledgements, and dead-letter handling stay out of the
request path.
"""

from __future__ import annotations

import json
import uuid

import redis
from redis import Redis

from src.core.config import settings


# Admission and XADD must be one Redis command. MAXLEN would be unsafe
# because it can evict a message still in the consumer group's PEL.
ENQUEUE_WITH_BACKPRESSURE_LUA = """
if tonumber(ARGV[1]) < 1 then
    return redis.error_reply('EMAIL_STREAM_MAXLEN must be positive')
end
if redis.call('XLEN', KEYS[1]) >= tonumber(ARGV[1]) then
    return 0
end
return redis.call('XADD', KEYS[1], '*', unpack(ARGV, 2))
"""


class EmailQueueFullError(RuntimeError):
    """The email queue is at capacity; no new job was accepted."""


def redis_client() -> Redis:
    """Create a short-lived Redis client for publishing email jobs."""
    return redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        health_check_interval=30,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


def enqueue_email(
    *,
    to: str,
    subject: str,
    html: str,
    text: str | None = None,
    email_type: str = "transactional",
    idempotency_key: str | None = None,
    max_attempts: int | None = None,
) -> str:
    """
    Publish one email job to the configured Redis Stream.

    The returned job ID is stable for the lifetime of the queued job and
    is also used as the default provider idempotency key. Retries keep the
    same key, preventing duplicate delivery when the provider receives the
    same request more than once.
    """
    recipient = to.strip()
    normalized_subject = subject.strip()

    if not recipient:
        raise ValueError(
            "Email recipient cannot be empty"
        )

    if not normalized_subject:
        raise ValueError(
            "Email subject cannot be empty"
        )

    if not html:
        raise ValueError(
            "Email HTML body cannot be empty"
        )

    job_id = uuid.uuid4().hex

    provider_key = (
        idempotency_key
        or job_id
    ).strip()

    if not provider_key:
        raise ValueError(
            "Email idempotency key cannot be empty"
        )

    attempts = (
        settings.EMAIL_WORKER_MAX_ATTEMPTS
        if max_attempts is None
        else max_attempts
    )

    attempts = max(
        1,
        min(
            int(attempts),
            settings.EMAIL_WORKER_MAX_ATTEMPTS,
        ),
    )

    fields: dict[str, str] = {
        "id": job_id,
        "type": (
            email_type.strip()
            or "transactional"
        ),
        "to": json.dumps(
            [recipient],
            separators=(",", ":"),
        ),
        "subject": normalized_subject,
        "html": html,
        "idempotency_key": provider_key,
        "attempt": "0",
        "max_attempts": str(
            attempts
        ),
    }

    if text:
        fields["text"] = text

    capacity = int(settings.EMAIL_STREAM_MAXLEN)
    if capacity < 1:
        raise ValueError("EMAIL_STREAM_MAXLEN must be positive")

    field_args = [
        field
        for name, value in fields.items()
        for field in (name, value)
    ]
    client = redis_client()

    try:
        result = client.eval(
            ENQUEUE_WITH_BACKPRESSURE_LUA,
            1,
            settings.EMAIL_STREAM_KEY,
            str(capacity),
            *field_args,
        )
    finally:
        client.close()

    if result == 0:
        raise EmailQueueFullError(
            "Email queue is full; email was not enqueued"
        )

    return job_id


def email_verification_template(
    name: str,
    url: str,
) -> str:
    return f"""
    <html>
      <body style="
        font-family:Arial,sans-serif;
        background:#090a0f;
        color:#f3f4f6;
        padding:40px
      ">
        <div style="
          max-width:560px;
          margin:auto;
          background:#12151e;
          padding:32px;
          border-radius:10px
        ">
          <h2>
            Verify your Temporal Intelligence account
          </h2>

          <p>
            Hello {name or 'there'},
          </p>

          <p>
            Please verify your email address
            before signing in.
          </p>

          <p>
            <a
              href="{url}"
              style="
                display:inline-block;
                padding:12px 18px;
                background:#7c8cf7;
                color:#090a0f;
                text-decoration:none;
                border-radius:6px
              "
            >
              Verify email
            </a>
          </p>

          <p>
            This link expires in 24 hours.
          </p>
        </div>
      </body>
    </html>
    """


def email_verification_code_template(
    name: str,
    code: str,
) -> str:
    """Render the six-digit email verification code message."""
    return f"""
    <html>
      <body style="
        margin:0;
        padding:40px 20px;
        background:#090a0f;
        color:#f3f4f6;
        font-family:Inter,Arial,sans-serif;
      ">
        <div style="
          max-width:560px;
          margin:0 auto;
          background:#12151e;
          border:1px solid rgba(255,255,255,.08);
          border-radius:12px;
          padding:32px;
        ">
          <div style="font-size:22px;font-weight:700;">
            Temporal <span style="color:#7c8cf7;">Intelligence</span>
          </div>

          <p style="margin-top:24px;">
            Hello {name or 'there'},
          </p>

          <p style="color:#a7afbc;line-height:1.6;">
            Use the verification code below to confirm your email address.
          </p>

          <div style="
            margin:28px 0;
            padding:18px;
            text-align:center;
            letter-spacing:10px;
            font-size:32px;
            font-weight:700;
            background:#090c12;
            border:1px solid rgba(124,140,247,.25);
            border-radius:10px;
          ">
            {code}
          </div>

          <p style="color:#727b8a;font-size:13px;line-height:1.6;">
            This code expires in 15 minutes. If you did not create this account,
            you can safely ignore this email.
          </p>
        </div>
      </body>
    </html>
    """


def reset_password_template(
    name: str,
    url: str,
) -> str:
    return f"""
    <html>
      <body style="
        font-family:Arial,sans-serif;
        background:#090a0f;
        color:#f3f4f6;
        padding:40px
      ">
        <div style="
          max-width:560px;
          margin:auto;
          background:#12151e;
          padding:32px;
          border-radius:10px
        ">
          <h2>
            Reset your Temporal Intelligence password
          </h2>

          <p>
            Hello {name or 'there'},
          </p>

          <p>
            We received a request to reset your
            password.
          </p>

          <p>
            <a
              href="{url}"
              style="
                display:inline-block;
                padding:12px 18px;
                background:#7c8cf7;
                color:#090a0f;
                text-decoration:none;
                border-radius:6px
              "
            >
              Reset password
            </a>
          </p>

          <p>
            This link expires in 1 hour.
            If you did not request this,
            you can safely ignore this email.
          </p>
        </div>
      </body>
    </html>
    """


def welcome_template(
    name: str,
) -> str:
    return f"""
    <html>
      <body style="
        font-family:Arial,sans-serif;
        background:#090a0f;
        color:#f3f4f6;
        padding:40px
      ">
        <div style="
          max-width:560px;
          margin:auto;
          background:#12151e;
          padding:32px;
          border-radius:10px
        ">
          <h2>
            Welcome to Temporal Intelligence
          </h2>

          <p>
            Hello {name or 'there'},
          </p>

          <p>
            Your account has been created.
            Verify your email to start using
            the console.
          </p>
        </div>
      </body>
    </html>
    """