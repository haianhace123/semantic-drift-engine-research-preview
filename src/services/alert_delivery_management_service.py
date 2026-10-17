"""
Tenant-scoped management for durable Alert deliveries.

This service owns operator-facing delivery reads and safe manual requeue
transitions. It does not perform external network I/O and does not commit
or roll back transactions.

The caller owns transaction boundaries.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from src.models.alert_delivery import (
    ALERT_DELIVERY_STATUS_DELIVERED,
    ALERT_DELIVERY_STATUS_FAILED,
    ALERT_DELIVERY_STATUS_PENDING,
    ALERT_DELIVERY_STATUS_PROCESSING,
    ALERT_DELIVERY_STATUS_RETRYABLE,
    AlertDelivery,
)
from src.services.alert_delivery_runtime_service import (
    DEFAULT_MAX_ATTEMPTS,
    utc_now,
)


ALERT_DELIVERY_STATUSES = frozenset(
    {
        ALERT_DELIVERY_STATUS_PENDING,
        ALERT_DELIVERY_STATUS_PROCESSING,
        ALERT_DELIVERY_STATUS_DELIVERED,
        ALERT_DELIVERY_STATUS_RETRYABLE,
        ALERT_DELIVERY_STATUS_FAILED,
    }
)

DEFAULT_DELIVERY_LIST_LIMIT = 100
MAX_DELIVERY_LIST_LIMIT = 500


class AlertDeliveryManagementError(ValueError):
    """Base error for delivery management operations."""


class AlertDeliveryNotFoundError(
    AlertDeliveryManagementError
):
    """Raised when a tenant-owned delivery does not exist."""


class AlertDeliveryConflictError(
    AlertDeliveryManagementError
):
    """Raised when a requested delivery transition is unsafe."""


class AlertDeliveryValidationError(
    AlertDeliveryManagementError
):
    """Raised when delivery management input is invalid."""


def _validate_status(
    status: str | None,
) -> str | None:
    if status is None:
        return None

    normalized = str(
        status
    ).strip().lower()

    if normalized not in ALERT_DELIVERY_STATUSES:
        raise AlertDeliveryValidationError(
            f"Unsupported AlertDelivery status: {normalized}"
        )

    return normalized


def _validate_pagination(
    *,
    limit: int,
    offset: int,
) -> None:
    if limit < 1:
        raise AlertDeliveryValidationError(
            "Delivery list limit must be at least 1."
        )

    if limit > MAX_DELIVERY_LIST_LIMIT:
        raise AlertDeliveryValidationError(
            "Delivery list limit cannot exceed "
            f"{MAX_DELIVERY_LIST_LIMIT}."
        )

    if offset < 0:
        raise AlertDeliveryValidationError(
            "Delivery list offset cannot be negative."
        )


def get_alert_delivery(
    db: Session,
    *,
    organization_id: uuid.UUID,
    delivery_id: uuid.UUID,
) -> AlertDelivery:
    delivery = (
        db.query(
            AlertDelivery
        )
        .filter(
            AlertDelivery.id
            == delivery_id,
            AlertDelivery.organization_id
            == organization_id,
        )
        .one_or_none()
    )

    if delivery is None:
        raise AlertDeliveryNotFoundError(
            "Alert delivery not found."
        )

    return delivery


def count_alert_deliveries(
    db: Session,
    *,
    organization_id: uuid.UUID,
    status: str | None = None,
) -> int:
    normalized_status = _validate_status(
        status
    )

    query = (
        db.query(
            AlertDelivery
        )
        .filter(
            AlertDelivery.organization_id
            == organization_id
        )
    )

    if normalized_status is not None:
        query = query.filter(
            AlertDelivery.status
            == normalized_status
        )

    return int(
        query.count()
    )


def list_alert_deliveries(
    db: Session,
    *,
    organization_id: uuid.UUID,
    status: str | None = None,
    limit: int = DEFAULT_DELIVERY_LIST_LIMIT,
    offset: int = 0,
) -> list[AlertDelivery]:
    normalized_status = _validate_status(
        status
    )

    _validate_pagination(
        limit=limit,
        offset=offset,
    )

    query = (
        db.query(
            AlertDelivery
        )
        .filter(
            AlertDelivery.organization_id
            == organization_id
        )
    )

    if normalized_status is not None:
        query = query.filter(
            AlertDelivery.status
            == normalized_status
        )

    return (
        query.order_by(
            AlertDelivery.created_at.desc(),
            AlertDelivery.id.desc(),
        )
        .offset(
            offset
        )
        .limit(
            limit
        )
        .all()
    )


def get_alert_delivery_retry_eligibility(
    delivery: AlertDelivery,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> tuple[bool, str | None]:
    if max_attempts < 1:
        raise AlertDeliveryValidationError(
            "Max attempts must be at least 1."
        )

    status = str(
        delivery.status or ""
    )
    attempt_count = int(
        delivery.attempt_count or 0
    )

    if (
        status
        == ALERT_DELIVERY_STATUS_PENDING
    ):
        return False, "already_pending"

    if (
        status
        == ALERT_DELIVERY_STATUS_PROCESSING
    ):
        return False, "currently_processing"

    if (
        status
        == ALERT_DELIVERY_STATUS_DELIVERED
    ):
        return False, "already_delivered"

    if status not in {
        ALERT_DELIVERY_STATUS_FAILED,
        ALERT_DELIVERY_STATUS_RETRYABLE,
    }:
        return False, "status_not_retryable"

    if attempt_count >= max_attempts:
        return False, "attempt_budget_exhausted"

    return True, None


def retry_alert_delivery(
    db: Session,
    *,
    organization_id: uuid.UUID | str,
    delivery_id: uuid.UUID | str,
    now: datetime | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> AlertDelivery:
    """
    Requeue a failed or retryable delivery without erasing attempt history.

    The row is locked so this transition cannot race with a worker claim.

    Manual retry is intentionally rejected once the configured attempt
    budget is exhausted. Resetting attempt_count would destroy useful
    operational history and weaken the worker's max-attempt invariant.
    """
    if max_attempts < 1:
        raise AlertDeliveryValidationError(
            "Max attempts must be at least 1."
        )

    delivery = (
        db.query(
            AlertDelivery
        )
        .filter(
            AlertDelivery.id
            == delivery_id,
            AlertDelivery.organization_id
            == organization_id,
        )
        .with_for_update()
        .one_or_none()
    )

    if delivery is None:
        raise AlertDeliveryNotFoundError(
            "Alert delivery not found."
        )

    (
        retry_allowed,
        retry_block_reason,
    ) = get_alert_delivery_retry_eligibility(
        delivery,
        max_attempts=max_attempts,
    )

    if not retry_allowed:
        conflict_messages = {
            "already_pending": (
                "Alert delivery is already pending."
            ),
            "currently_processing": (
                "Alert delivery is currently processing."
            ),
            "already_delivered": (
                "Delivered AlertDelivery cannot be retried."
            ),
            "attempt_budget_exhausted": (
                "Alert delivery attempt budget is exhausted."
            ),
            "status_not_retryable": (
                "Alert delivery is not retryable."
            ),
        }

        raise AlertDeliveryConflictError(
            conflict_messages[
                retry_block_reason
                or "status_not_retryable"
            ]
        )

    delivery.status = (
        ALERT_DELIVERY_STATUS_RETRYABLE
    )

    delivery.claimed_at = None

    delivery.next_attempt_at = (
        now or utc_now()
    )

    db.flush()

    return delivery