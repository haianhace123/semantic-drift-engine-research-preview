from __future__ import annotations

import uuid
from datetime import datetime, timezone
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
from src.services.alert_delivery_management_service import (
    MAX_DELIVERY_LIST_LIMIT,
    AlertDeliveryConflictError,
    AlertDeliveryNotFoundError,
    AlertDeliveryValidationError,
    count_alert_deliveries,
    get_alert_delivery,
    list_alert_deliveries,
    retry_alert_delivery,
)


def _delivery(
    *,
    status: str = ALERT_DELIVERY_STATUS_FAILED,
    attempt_count: int = 1,
) -> AlertDelivery:
    return AlertDelivery(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        alert_id=uuid.uuid4(),
        destination_type="webhook",
        destination_id=uuid.uuid4(),
        delivery_key=uuid.uuid4().hex,
        status=status,
        payload={"event": "alert.created"},
        attempt_count=attempt_count,
    )


def _db_for_get(
    delivery: AlertDelivery | None,
) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = (
        delivery
    )
    return db


def _db_for_retry(
    delivery: AlertDelivery | None,
) -> tuple[MagicMock, MagicMock]:
    db = MagicMock()
    filtered = db.query.return_value.filter.return_value
    locked = filtered.with_for_update.return_value
    locked.one_or_none.return_value = delivery
    return db, filtered


def test_get_alert_delivery_returns_tenant_owned_row() -> None:
    delivery = _delivery()
    db = _db_for_get(delivery)

    result = get_alert_delivery(
        db,
        organization_id=delivery.organization_id,
        delivery_id=delivery.id,
    )

    assert result is delivery
    db.query.assert_called_once_with(AlertDelivery)

    filter_args = db.query.return_value.filter.call_args.args
    assert len(filter_args) == 2
    assert filter_args[0].right.value == delivery.id
    assert filter_args[1].right.value == delivery.organization_id


def test_get_alert_delivery_raises_not_found() -> None:
    db = _db_for_get(None)

    with pytest.raises(
        AlertDeliveryNotFoundError,
        match="not found",
    ):
        get_alert_delivery(
            db,
            organization_id=uuid.uuid4(),
            delivery_id=uuid.uuid4(),
        )


def test_count_alert_deliveries_is_tenant_scoped() -> None:
    organization_id = uuid.uuid4()
    db = MagicMock()

    query = db.query.return_value
    tenant_query = query.filter.return_value
    tenant_query.count.return_value = 7

    result = count_alert_deliveries(
        db,
        organization_id=organization_id,
    )

    assert result == 7
    db.query.assert_called_once_with(
        AlertDelivery
    )

    tenant_expression = (
        query.filter.call_args.args[0]
    )
    assert (
        tenant_expression.right.value
        == organization_id
    )
    tenant_query.count.assert_called_once_with()


def test_count_alert_deliveries_normalizes_status() -> None:
    organization_id = uuid.uuid4()
    db = MagicMock()

    query = db.query.return_value
    tenant_query = query.filter.return_value
    status_query = (
        tenant_query.filter.return_value
    )
    status_query.count.return_value = 3

    result = count_alert_deliveries(
        db,
        organization_id=organization_id,
        status="  RETRYABLE  ",
    )

    assert result == 3

    status_expression = (
        tenant_query.filter.call_args.args[0]
    )
    assert (
        status_expression.right.value
        == ALERT_DELIVERY_STATUS_RETRYABLE
    )
    status_query.count.assert_called_once_with()


def test_count_alert_deliveries_rejects_invalid_status() -> None:
    db = MagicMock()

    with pytest.raises(
        AlertDeliveryValidationError,
        match="Unsupported",
    ):
        count_alert_deliveries(
            db,
            organization_id=uuid.uuid4(),
            status="unknown",
        )

    db.query.assert_not_called()


def test_list_alert_deliveries_is_tenant_scoped_and_ordered() -> None:
    organization_id = uuid.uuid4()
    deliveries = [_delivery(), _delivery()]
    db = MagicMock()

    query = db.query.return_value
    tenant_query = query.filter.return_value
    ordered = tenant_query.order_by.return_value
    offset_query = ordered.offset.return_value
    limited = offset_query.limit.return_value
    limited.all.return_value = deliveries

    result = list_alert_deliveries(
        db,
        organization_id=organization_id,
        limit=25,
        offset=10,
    )

    assert result == deliveries
    db.query.assert_called_once_with(AlertDelivery)

    tenant_expression = query.filter.call_args.args[0]
    assert tenant_expression.right.value == organization_id

    order_by_args = tenant_query.order_by.call_args.args

    assert len(order_by_args) == 2
    assert str(order_by_args[0]) == "alert_deliveries.created_at DESC"
    assert str(order_by_args[1]) == "alert_deliveries.id DESC"
    ordered.offset.assert_called_once_with(10)
    offset_query.limit.assert_called_once_with(25)


def test_list_normalizes_status_before_filtering() -> None:
    db = MagicMock()

    tenant_query = db.query.return_value.filter.return_value
    status_query = tenant_query.filter.return_value
    ordered = status_query.order_by.return_value
    offset_query = ordered.offset.return_value
    limited = offset_query.limit.return_value
    limited.all.return_value = []

    result = list_alert_deliveries(
        db,
        organization_id=uuid.uuid4(),
        status="  RETRYABLE  ",
    )

    assert result == []
    assert tenant_query.filter.call_count == 1

    status_expression = tenant_query.filter.call_args.args[0]
    assert status_expression.right.value == ALERT_DELIVERY_STATUS_RETRYABLE


def test_list_rejects_invalid_status_before_query() -> None:
    db = MagicMock()

    with pytest.raises(
        AlertDeliveryValidationError,
        match="Unsupported",
    ):
        list_alert_deliveries(
            db,
            organization_id=uuid.uuid4(),
            status="unknown",
        )

    db.query.assert_not_called()


@pytest.mark.parametrize(
    ("limit", "offset"),
    [
        (0, 0),
        (MAX_DELIVERY_LIST_LIMIT + 1, 0),
        (1, -1),
    ],
)
def test_list_rejects_invalid_pagination(
    limit: int,
    offset: int,
) -> None:
    db = MagicMock()

    with pytest.raises(AlertDeliveryValidationError):
        list_alert_deliveries(
            db,
            organization_id=uuid.uuid4(),
            limit=limit,
            offset=offset,
        )

    db.query.assert_not_called()


def test_retry_failed_delivery_requeues_without_erasing_history() -> None:
    delivery = _delivery(
        status=ALERT_DELIVERY_STATUS_FAILED,
        attempt_count=2,
    )
    claimed_at = datetime(
        2026,
        9,
        13,
        2,
        0,
        tzinfo=timezone.utc,
    )
    delivery.claimed_at = claimed_at
    delivery.last_error = "upstream timeout"

    db, filtered = _db_for_retry(delivery)
    now = datetime(
        2026,
        9,
        13,
        3,
        0,
        tzinfo=timezone.utc,
    )

    result = retry_alert_delivery(
        db,
        organization_id=delivery.organization_id,
        delivery_id=delivery.id,
        now=now,
        max_attempts=5,
    )

    assert result is delivery
    assert delivery.status == ALERT_DELIVERY_STATUS_RETRYABLE
    assert delivery.claimed_at is None
    assert delivery.next_attempt_at == now
    assert delivery.attempt_count == 2
    assert delivery.last_error == "upstream timeout"
    filter_args = db.query.return_value.filter.call_args.args
    assert len(filter_args) == 2
    assert filter_args[0].right.value == delivery.id
    assert filter_args[1].right.value == delivery.organization_id
    filtered.with_for_update.assert_called_once_with()
    db.flush.assert_called_once_with()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_retry_retryable_delivery_can_be_requeued() -> None:
    delivery = _delivery(
        status=ALERT_DELIVERY_STATUS_RETRYABLE,
        attempt_count=1,
    )
    db, _ = _db_for_retry(delivery)
    now = datetime(
        2026,
        9,
        13,
        4,
        0,
        tzinfo=timezone.utc,
    )

    result = retry_alert_delivery(
        db,
        organization_id=delivery.organization_id,
        delivery_id=delivery.id,
        now=now,
    )

    assert result is delivery
    assert delivery.status == ALERT_DELIVERY_STATUS_RETRYABLE
    assert delivery.next_attempt_at == now
    db.flush.assert_called_once_with()


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (ALERT_DELIVERY_STATUS_PENDING, "already pending"),
        (ALERT_DELIVERY_STATUS_PROCESSING, "currently processing"),
        (ALERT_DELIVERY_STATUS_DELIVERED, "cannot be retried"),
        ("unexpected", "not retryable"),
    ],
)
def test_retry_rejects_unsafe_statuses(
    status: str,
    message: str,
) -> None:
    delivery = _delivery(status=status)
    db, filtered = _db_for_retry(delivery)

    with pytest.raises(
        AlertDeliveryConflictError,
        match=message,
    ):
        retry_alert_delivery(
            db,
            organization_id=delivery.organization_id,
            delivery_id=delivery.id,
        )

    filtered.with_for_update.assert_called_once_with()
    db.flush.assert_not_called()
    db.commit.assert_not_called()


def test_retry_rejects_exhausted_attempt_budget() -> None:
    delivery = _delivery(
        status=ALERT_DELIVERY_STATUS_FAILED,
        attempt_count=5,
    )
    db, _ = _db_for_retry(delivery)

    with pytest.raises(
        AlertDeliveryConflictError,
        match="budget is exhausted",
    ):
        retry_alert_delivery(
            db,
            organization_id=delivery.organization_id,
            delivery_id=delivery.id,
            max_attempts=5,
        )

    db.flush.assert_not_called()


def test_retry_rejects_invalid_max_attempts_before_query() -> None:
    db = MagicMock()

    with pytest.raises(
        AlertDeliveryValidationError,
        match="Max attempts",
    ):
        retry_alert_delivery(
            db,
            organization_id=uuid.uuid4(),
            delivery_id=uuid.uuid4(),
            max_attempts=0,
        )

    db.query.assert_not_called()


def test_retry_missing_or_foreign_delivery_is_not_found() -> None:
    db, filtered = _db_for_retry(None)

    with pytest.raises(
        AlertDeliveryNotFoundError,
        match="not found",
    ):
        retry_alert_delivery(
            db,
            organization_id=uuid.uuid4(),
            delivery_id=uuid.uuid4(),
        )

    filtered.with_for_update.assert_called_once_with()
    db.flush.assert_not_called()


def test_retry_uses_utc_now_when_now_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery = _delivery(
        status=ALERT_DELIVERY_STATUS_FAILED,
        attempt_count=1,
    )
    db, _ = _db_for_retry(delivery)
    expected_now = datetime(
        2026,
        9,
        13,
        5,
        0,
        tzinfo=timezone.utc,
    )

    monkeypatch.setattr(
        "src.services.alert_delivery_management_service.utc_now",
        lambda: expected_now,
    )

    retry_alert_delivery(
        db,
        organization_id=delivery.organization_id,
        delivery_id=delivery.id,
    )

    assert delivery.next_attempt_at == expected_now
