from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.models.alert_delivery import (
    ALERT_DELIVERY_STATUS_DELIVERED,
    ALERT_DELIVERY_STATUS_FAILED,
    ALERT_DELIVERY_STATUS_PENDING,
    ALERT_DELIVERY_STATUS_PROCESSING,
    ALERT_DELIVERY_STATUS_RETRYABLE,
    AlertDelivery,
)
from src.services.alert_delivery_runtime_service import (
    AlertDeliveryRuntimeError,
    claim_alert_deliveries,
    mark_delivery_attempt_started,
    mark_delivery_delivered,
    mark_delivery_failed,
    mark_delivery_retryable,
    retry_delay_seconds,
)


def _processing_delivery(
    *,
    attempt_count: int = 1,
) -> AlertDelivery:
    return AlertDelivery(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        alert_id=uuid.uuid4(),
        destination_type="webhook",
        destination_id=uuid.uuid4(),
        delivery_key="delivery-key",
        status=ALERT_DELIVERY_STATUS_PROCESSING,
        payload={"event": "alert.created"},
        attempt_count=attempt_count,
    )


def _db_for_delivery(
    delivery: AlertDelivery | None,
) -> MagicMock:
    db = MagicMock()

    query = db.query.return_value
    filtered = query.filter.return_value
    refreshed = filtered.populate_existing.return_value
    locked = refreshed.with_for_update.return_value

    locked.one_or_none.return_value = delivery

    return db


def test_retry_delay_is_exponential_and_capped() -> None:
    assert retry_delay_seconds(1) == 30
    assert retry_delay_seconds(2) == 60
    assert retry_delay_seconds(3) == 120
    assert retry_delay_seconds(20) == 3600


def test_retry_delay_rejects_invalid_attempt() -> None:
    with pytest.raises(
        AlertDeliveryRuntimeError,
        match="at least 1",
    ):
        retry_delay_seconds(0)


def test_claim_marks_rows_processing_and_increments_attempts() -> None:
    now = datetime(
        2026,
        9,
        12,
        1,
        0,
        tzinfo=timezone.utc,
    )

    pending = AlertDelivery(
        id=uuid.uuid4(),
        status=ALERT_DELIVERY_STATUS_PENDING,
        attempt_count=0,
    )

    retryable = AlertDelivery(
        id=uuid.uuid4(),
        status=ALERT_DELIVERY_STATUS_RETRYABLE,
        attempt_count=2,
        next_attempt_at=now - timedelta(seconds=1),
    )

    stale = AlertDelivery(
        id=uuid.uuid4(),
        status=ALERT_DELIVERY_STATUS_PROCESSING,
        attempt_count=3,
        claimed_at=now - timedelta(minutes=10),
    )

    db = MagicMock()

    query = db.query.return_value
    filtered = query.filter.return_value
    ordered = filtered.order_by.return_value
    locked = ordered.with_for_update.return_value
    limited = locked.limit.return_value

    limited.all.return_value = [
        pending,
        retryable,
        stale,
    ]

    result = claim_alert_deliveries(
        db,
        limit=3,
        now=now,
        claim_timeout_seconds=300,
    )

    assert result == (
        pending,
        retryable,
        stale,
    )

    assert pending.status == ALERT_DELIVERY_STATUS_PROCESSING
    assert pending.attempt_count == 1
    assert pending.claimed_at == now

    assert retryable.status == ALERT_DELIVERY_STATUS_PROCESSING
    assert retryable.attempt_count == 3
    assert retryable.claimed_at == now
    assert retryable.next_attempt_at is None

    assert stale.status == ALERT_DELIVERY_STATUS_PROCESSING
    assert stale.attempt_count == 4
    assert stale.claimed_at == now

    ordered.with_for_update.assert_called_once_with(
        skip_locked=True
    )
    locked.limit.assert_called_once_with(3)
    db.flush.assert_called_once_with()


def test_claim_rejects_invalid_limit() -> None:
    db = MagicMock()

    with pytest.raises(
        AlertDeliveryRuntimeError,
        match="limit",
    ):
        claim_alert_deliveries(
            db,
            limit=0,
        )

    db.query.assert_not_called()

def test_claim_rejects_invalid_max_attempts() -> None:
    db = MagicMock()

    with pytest.raises(
        AlertDeliveryRuntimeError,
        match="Max attempts",
    ):
        claim_alert_deliveries(
            db,
            limit=1,
            max_attempts=0,
        )

    db.query.assert_not_called()

def test_mark_attempt_started_records_http_attempt_time() -> None:
    delivery = _processing_delivery()
    db = _db_for_delivery(delivery)

    attempted_at = datetime(
        2026,
        9,
        12,
        1,
        5,
        tzinfo=timezone.utc,
    )

    result = mark_delivery_attempt_started(
        db,
        delivery_id=delivery.id,
        expected_attempt_count=1,
        attempted_at=attempted_at,
    )

    assert result is delivery
    assert delivery.last_attempt_at == attempted_at
    db.flush.assert_called_once_with()


def test_mark_delivered_clears_retry_state() -> None:
    delivery = _processing_delivery()
    delivery.claimed_at = datetime(
        2026,
        9,
        12,
        1,
        0,
        tzinfo=timezone.utc,
    )
    delivery.next_attempt_at = datetime(
        2026,
        9,
        12,
        1,
        10,
        tzinfo=timezone.utc,
    )
    delivery.last_error = "old error"

    db = _db_for_delivery(delivery)

    delivered_at = datetime(
        2026,
        9,
        12,
        1,
        6,
        tzinfo=timezone.utc,
    )

    result = mark_delivery_delivered(
        db,
        delivery_id=delivery.id,
        expected_attempt_count=1,
        delivered_at=delivered_at,
    )

    assert result is delivery
    assert delivery.status == ALERT_DELIVERY_STATUS_DELIVERED
    assert delivery.delivered_at == delivered_at
    assert delivery.claimed_at is None
    assert delivery.next_attempt_at is None
    assert delivery.last_error is None
    db.flush.assert_called_once_with()


def test_mark_retryable_schedules_backoff() -> None:
    delivery = _processing_delivery(
        attempt_count=2,
    )
    db = _db_for_delivery(delivery)

    now = datetime(
        2026,
        9,
        12,
        1,
        10,
        tzinfo=timezone.utc,
    )

    result = mark_delivery_retryable(
        db,
        delivery_id=delivery.id,
        expected_attempt_count=2,
        error="temporary outage",
        now=now,
    )

    assert result is delivery
    assert delivery.status == ALERT_DELIVERY_STATUS_RETRYABLE
    assert delivery.claimed_at is None
    assert delivery.last_error == "temporary outage"
    assert delivery.next_attempt_at == (
        now + timedelta(seconds=60)
    )
    db.flush.assert_called_once_with()


def test_mark_retryable_becomes_failed_at_max_attempts() -> None:
    delivery = _processing_delivery(
        attempt_count=5,
    )
    db = _db_for_delivery(delivery)

    result = mark_delivery_retryable(
        db,
        delivery_id=delivery.id,
        expected_attempt_count=5,
        error="still unavailable",
        max_attempts=5,
    )

    assert result is delivery
    assert delivery.status == ALERT_DELIVERY_STATUS_FAILED
    assert delivery.claimed_at is None
    assert delivery.next_attempt_at is None
    assert delivery.last_error == "still unavailable"
    db.flush.assert_called_once_with()


def test_mark_failed_is_terminal() -> None:
    delivery = _processing_delivery()
    db = _db_for_delivery(delivery)

    result = mark_delivery_failed(
        db,
        delivery_id=delivery.id,
        expected_attempt_count=1,
        error="HTTP 400",
    )

    assert result is delivery
    assert delivery.status == ALERT_DELIVERY_STATUS_FAILED
    assert delivery.claimed_at is None
    assert delivery.next_attempt_at is None
    assert delivery.last_error == "HTTP 400"
    db.flush.assert_called_once_with()


def test_runtime_transition_rejects_non_processing_row() -> None:
    delivery = _processing_delivery()
    delivery.status = ALERT_DELIVERY_STATUS_PENDING

    db = _db_for_delivery(delivery)

    with pytest.raises(
        AlertDeliveryRuntimeError,
        match="not in processing",
    ):
        mark_delivery_delivered(
            db,
            delivery_id=delivery.id,
            expected_attempt_count=1,
        )

    db.flush.assert_not_called()

def test_runtime_transition_rejects_stale_attempt() -> None:
    delivery = _processing_delivery(
        attempt_count=2,
    )
    db = _db_for_delivery(delivery)

    with pytest.raises(
        AlertDeliveryRuntimeError,
        match="no longer current",
    ):
        mark_delivery_delivered(
            db,
            delivery_id=delivery.id,
            expected_attempt_count=1,
        )

    assert (
        delivery.status
        == ALERT_DELIVERY_STATUS_PROCESSING
    )
    db.flush.assert_not_called()


def test_runtime_transition_rejects_invalid_expected_attempt() -> None:
    delivery = _processing_delivery(
        attempt_count=1,
    )
    db = _db_for_delivery(delivery)

    with pytest.raises(
        AlertDeliveryRuntimeError,
        match="at least 1",
    ):
        mark_delivery_failed(
            db,
            delivery_id=delivery.id,
            expected_attempt_count=0,
            error="should not apply",
        )

    db.query.assert_not_called()
    db.flush.assert_not_called()