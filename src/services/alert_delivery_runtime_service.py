"""
Durable runtime state transitions for Alert delivery.

This service owns database-backed delivery claiming and state changes.

Responsibilities:
- claim ready AlertDelivery rows safely across concurrent workers;
- recover stale processing claims;
- track delivery attempts;
- schedule deterministic retries;
- persist delivered or terminal-failure state.

Non-responsibilities:
- no external network I/O;
- no webhook payload construction;
- no destination configuration lookup;
- no commit / rollback.

The caller owns transaction boundaries.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from src.models.alert_delivery import (
    ALERT_DELIVERY_STATUS_DELIVERED,
    ALERT_DELIVERY_STATUS_FAILED,
    ALERT_DELIVERY_STATUS_PENDING,
    ALERT_DELIVERY_STATUS_PROCESSING,
    ALERT_DELIVERY_STATUS_RETRYABLE,
    AlertDelivery,
)


DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_RETRY_BASE_SECONDS = 30
DEFAULT_RETRY_MAX_SECONDS = 3600
DEFAULT_CLAIM_TIMEOUT_SECONDS = 300

MAX_ERROR_LENGTH = 2000


class AlertDeliveryRuntimeError(ValueError):
    """Raised when a delivery runtime transition is invalid."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def delivery_ready_retry_predicate(
    *,
    now: datetime,
):
    return and_(
        AlertDelivery.status
        == ALERT_DELIVERY_STATUS_RETRYABLE,
        or_(
            AlertDelivery.next_attempt_at.is_(None),
            AlertDelivery.next_attempt_at <= now,
        ),
    )


def delivery_stale_processing_predicate(
    *,
    stale_before: datetime,
):
    return and_(
        AlertDelivery.status
        == ALERT_DELIVERY_STATUS_PROCESSING,
        or_(
            AlertDelivery.claimed_at.is_(None),
            AlertDelivery.claimed_at
            <= stale_before,
        ),
    )


def delivery_claimable_predicate(
    *,
    now: datetime,
    stale_before: datetime,
    max_attempts: int,
):
    return and_(
        AlertDelivery.attempt_count
        < max_attempts,
        or_(
            AlertDelivery.status
            == ALERT_DELIVERY_STATUS_PENDING,
            delivery_ready_retry_predicate(
                now=now
            ),
            delivery_stale_processing_predicate(
                stale_before=stale_before
            ),
        ),
    )


def delivery_exhausted_predicate(
    *,
    stale_before: datetime,
    max_attempts: int,
):
    return and_(
        AlertDelivery.attempt_count
        >= max_attempts,
        or_(
            AlertDelivery.status
            == ALERT_DELIVERY_STATUS_PENDING,
            AlertDelivery.status
            == ALERT_DELIVERY_STATUS_RETRYABLE,
            delivery_stale_processing_predicate(
                stale_before=stale_before
            ),
        ),
    )


def retry_delay_seconds(
    attempt: int,
    *,
    base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
    max_seconds: int = DEFAULT_RETRY_MAX_SECONDS,
) -> int:
    """
    Return deterministic exponential retry delay.

    Attempt 1 uses base_seconds, attempt 2 doubles it, and so on,
    capped at max_seconds.
    """
    if attempt < 1:
        raise AlertDeliveryRuntimeError(
            "Retry attempt must be at least 1."
        )

    if base_seconds < 1:
        raise AlertDeliveryRuntimeError(
            "Retry base seconds must be at least 1."
        )

    if max_seconds < base_seconds:
        raise AlertDeliveryRuntimeError(
            "Retry max seconds must be greater than or equal to "
            "retry base seconds."
        )

    return min(
        base_seconds * (2 ** max(0, attempt - 1)),
        max_seconds,
    )


def _normalize_error(error: str) -> str:
    normalized = str(error).strip()

    if not normalized:
        normalized = "Alert delivery failed."

    return normalized[:MAX_ERROR_LENGTH]


def _load_processing_delivery(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    expected_attempt_count: int,
) -> AlertDelivery:
    if expected_attempt_count < 1:
        raise AlertDeliveryRuntimeError(
            "Expected attempt count must be at least 1."
        )

    delivery = (
        db.query(AlertDelivery)
        .filter(AlertDelivery.id == delivery_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )

    if delivery is None:
        raise AlertDeliveryRuntimeError(
            "AlertDelivery does not exist."
        )

    if delivery.status != ALERT_DELIVERY_STATUS_PROCESSING:
        raise AlertDeliveryRuntimeError(
            "AlertDelivery is not in processing state."
        )

    if int(delivery.attempt_count or 0) != expected_attempt_count:
        raise AlertDeliveryRuntimeError(
            "AlertDelivery attempt is no longer current."
        )

    return delivery


def claim_alert_deliveries(
    db: Session,
    *,
    limit: int,
    now: datetime | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    claim_timeout_seconds: int = DEFAULT_CLAIM_TIMEOUT_SECONDS,
) -> tuple[AlertDelivery, ...]:
    """
    Atomically claim ready AlertDelivery rows.

    Claimable rows are:
    - pending;
    - retryable with no schedule or a due next_attempt_at;
    - processing with a stale or missing claim timestamp.

    PostgreSQL FOR UPDATE SKIP LOCKED prevents concurrent workers
    from claiming the same row.

    attempt_count is incremented when a claim is acquired. A worker
    crash therefore consumes an attempt instead of allowing an
    indefinitely crashable delivery to remain at the same count.

    The caller must commit the claim transaction before performing
    external network I/O.
    """
    if limit < 1:
        raise AlertDeliveryRuntimeError(
            "Claim limit must be at least 1."
        )

    if max_attempts < 1:
        raise AlertDeliveryRuntimeError(
            "Max attempts must be at least 1."
        )

    if claim_timeout_seconds < 1:
        raise AlertDeliveryRuntimeError(
            "Claim timeout seconds must be at least 1."
        )

    effective_now = now or utc_now()
    stale_before = effective_now - timedelta(
        seconds=claim_timeout_seconds
    )

    claimable = delivery_claimable_predicate(
        now=effective_now,
        stale_before=stale_before,
        max_attempts=max_attempts,
    )

    deliveries = (
        db.query(AlertDelivery)
        .filter(
            claimable
        )
        .order_by(
            AlertDelivery.created_at.asc(),
            AlertDelivery.id.asc(),
        )
        .with_for_update(skip_locked=True)
        .limit(limit)
        .all()
    )

    for delivery in deliveries:
        delivery.status = ALERT_DELIVERY_STATUS_PROCESSING
        delivery.claimed_at = effective_now
        delivery.next_attempt_at = None
        delivery.attempt_count = (
            int(delivery.attempt_count or 0) + 1
        )

    db.flush()

    return tuple(deliveries)

def finalize_exhausted_alert_deliveries(
    db: Session,
    *,
    limit: int,
    now: datetime | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    claim_timeout_seconds: int = DEFAULT_CLAIM_TIMEOUT_SECONDS,
) -> tuple[AlertDelivery, ...]:
    """
    Terminalize exhausted deliveries that can no longer be attempted.

    Eligible rows are:
    - pending with attempt_count >= max_attempts;
    - retryable with attempt_count >= max_attempts;
    - processing with attempt_count >= max_attempts when its claim is
      stale or missing.

    Fresh processing rows are intentionally excluded because the
    current worker may still complete the final allowed attempt.

    PostgreSQL FOR UPDATE SKIP LOCKED keeps recovery safe across
    concurrent workers.

    The caller owns commit / rollback.
    """
    if limit < 1:
        raise AlertDeliveryRuntimeError(
            "Finalize limit must be at least 1."
        )

    if max_attempts < 1:
        raise AlertDeliveryRuntimeError(
            "Max attempts must be at least 1."
        )

    if claim_timeout_seconds < 1:
        raise AlertDeliveryRuntimeError(
            "Claim timeout seconds must be at least 1."
        )

    effective_now = now or utc_now()

    stale_before = effective_now - timedelta(
        seconds=claim_timeout_seconds
    )

    exhausted = delivery_exhausted_predicate(
        stale_before=stale_before,
        max_attempts=max_attempts,
    )

    deliveries = (
        db.query(AlertDelivery)
        .filter(
            exhausted
        )
        .order_by(
            AlertDelivery.created_at.asc(),
            AlertDelivery.id.asc(),
        )
        .with_for_update(skip_locked=True)
        .limit(limit)
        .all()
    )

    for delivery in deliveries:
        delivery.status = ALERT_DELIVERY_STATUS_FAILED
        delivery.claimed_at = None
        delivery.next_attempt_at = None

        if not str(delivery.last_error or "").strip():
            delivery.last_error = (
                "Maximum delivery attempts exhausted."
            )

    db.flush()

    return tuple(deliveries)

def mark_delivery_attempt_started(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    expected_attempt_count: int,
    attempted_at: datetime | None = None,
) -> AlertDelivery:
    """
    Record the moment external delivery I/O begins.

    expected_attempt_count acts as a fencing token. A stale worker
    cannot mutate a delivery after a newer claim has incremented the
    attempt count.
    """
    delivery = _load_processing_delivery(
        db,
        delivery_id=delivery_id,
        expected_attempt_count=expected_attempt_count,
    )

    delivery.last_attempt_at = attempted_at or utc_now()

    db.flush()

    return delivery


def mark_delivery_delivered(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    expected_attempt_count: int,
    delivered_at: datetime | None = None,
) -> AlertDelivery:
    """Mark the current processing attempt as successfully delivered."""
    delivery = _load_processing_delivery(
        db,
        delivery_id=delivery_id,
        expected_attempt_count=expected_attempt_count,
    )

    effective_delivered_at = delivered_at or utc_now()

    delivery.status = ALERT_DELIVERY_STATUS_DELIVERED
    delivery.delivered_at = effective_delivered_at
    delivery.claimed_at = None
    delivery.next_attempt_at = None
    delivery.last_error = None

    db.flush()

    return delivery


def mark_delivery_retryable(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    expected_attempt_count: int,
    error: str,
    now: datetime | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
    retry_max_seconds: int = DEFAULT_RETRY_MAX_SECONDS,
) -> AlertDelivery:
    """
    Schedule a transient delivery failure for retry.

    If the delivery has already exhausted max_attempts, it transitions
    directly to terminal failed state instead.
    """
    if max_attempts < 1:
        raise AlertDeliveryRuntimeError(
            "Max attempts must be at least 1."
        )

    delivery = _load_processing_delivery(
        db,
        delivery_id=delivery_id,
        expected_attempt_count=expected_attempt_count,
    )

    effective_now = now or utc_now()
    attempt_count = int(delivery.attempt_count or 0)

    if attempt_count < 1:
        raise AlertDeliveryRuntimeError(
            "Processing AlertDelivery must have at least one attempt."
        )

    delivery.claimed_at = None
    delivery.last_error = _normalize_error(error)

    if attempt_count >= max_attempts:
        delivery.status = ALERT_DELIVERY_STATUS_FAILED
        delivery.next_attempt_at = None

        db.flush()
        return delivery

    delay = retry_delay_seconds(
        attempt_count,
        base_seconds=retry_base_seconds,
        max_seconds=retry_max_seconds,
    )

    delivery.status = ALERT_DELIVERY_STATUS_RETRYABLE
    delivery.next_attempt_at = effective_now + timedelta(
        seconds=delay
    )

    db.flush()

    return delivery


def mark_delivery_failed(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    expected_attempt_count: int,
    error: str,
) -> AlertDelivery:
    """Mark the current processing attempt as permanently failed."""
    delivery = _load_processing_delivery(
        db,
        delivery_id=delivery_id,
        expected_attempt_count=expected_attempt_count,
    )

    delivery.status = ALERT_DELIVERY_STATUS_FAILED
    delivery.claimed_at = None
    delivery.next_attempt_at = None
    delivery.last_error = _normalize_error(error)

    db.flush()

    return delivery