"""
Tenant-scoped operational visibility for durable Alert deliveries.

This service derives queue health from the same durable state and runtime
policy used by Alert delivery workers. It performs read-only database
queries, does not acquire delivery claims, and does not own transaction
boundaries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, func
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
    DEFAULT_CLAIM_TIMEOUT_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    delivery_claimable_predicate,
    delivery_exhausted_predicate,
    delivery_stale_processing_predicate,
    utc_now,
)


@dataclass(frozen=True)
class AlertDeliveryOperationalSummary:
    organization_id: uuid.UUID
    observed_at: datetime

    total_count: int

    pending_count: int
    processing_count: int
    retryable_count: int
    failed_count: int
    delivered_count: int

    claimable_count: int
    stale_processing_count: int
    exhausted_count: int

    total_attempt_count: int

    oldest_pending_at: datetime | None
    oldest_retryable_at: datetime | None
    oldest_processing_claimed_at: datetime | None


def get_alert_delivery_operational_summary(
    db: Session,
    *,
    organization_id: uuid.UUID,
    now: datetime | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    claim_timeout_seconds: int = DEFAULT_CLAIM_TIMEOUT_SECONDS,
) -> AlertDeliveryOperationalSummary:
    """
    Return one tenant-scoped snapshot of durable delivery queue health.

    claimable_count mirrors the eligibility predicate used by
    claim_alert_deliveries().

    exhausted_count mirrors the rows eligible for terminalization by
    finalize_exhausted_alert_deliveries(). Fresh processing rows on their
    final allowed attempt are intentionally excluded.
    """
    if max_attempts < 1:
        raise ValueError(
            "Max attempts must be at least 1."
        )

    if claim_timeout_seconds < 1:
        raise ValueError(
            "Claim timeout seconds must be at least 1."
        )

    effective_now = now or utc_now()

    stale_before = (
        effective_now
        - timedelta(
            seconds=claim_timeout_seconds
        )
    )

    stale_processing = (
        delivery_stale_processing_predicate(
            stale_before=stale_before
        )
    )

    claimable = delivery_claimable_predicate(
        now=effective_now,
        stale_before=stale_before,
        max_attempts=max_attempts,
    )

    exhausted = delivery_exhausted_predicate(
        stale_before=stale_before,
        max_attempts=max_attempts,
    )

    row = (
        db.query(
            func.count(
                AlertDelivery.id
            ).label(
                "total_count"
            ),
            func.sum(
                case(
                    (
                        AlertDelivery.status
                        == ALERT_DELIVERY_STATUS_PENDING,
                        1,
                    ),
                    else_=0,
                )
            ).label(
                "pending_count"
            ),
            func.sum(
                case(
                    (
                        AlertDelivery.status
                        == ALERT_DELIVERY_STATUS_PROCESSING,
                        1,
                    ),
                    else_=0,
                )
            ).label(
                "processing_count"
            ),
            func.sum(
                case(
                    (
                        AlertDelivery.status
                        == ALERT_DELIVERY_STATUS_RETRYABLE,
                        1,
                    ),
                    else_=0,
                )
            ).label(
                "retryable_count"
            ),
            func.sum(
                case(
                    (
                        AlertDelivery.status
                        == ALERT_DELIVERY_STATUS_FAILED,
                        1,
                    ),
                    else_=0,
                )
            ).label(
                "failed_count"
            ),
            func.sum(
                case(
                    (
                        AlertDelivery.status
                        == ALERT_DELIVERY_STATUS_DELIVERED,
                        1,
                    ),
                    else_=0,
                )
            ).label(
                "delivered_count"
            ),
            func.sum(
                case(
                    (
                        claimable,
                        1,
                    ),
                    else_=0,
                )
            ).label(
                "claimable_count"
            ),
            func.sum(
                case(
                    (
                        stale_processing,
                        1,
                    ),
                    else_=0,
                )
            ).label(
                "stale_processing_count"
            ),
            func.sum(
                case(
                    (
                        exhausted,
                        1,
                    ),
                    else_=0,
                )
            ).label(
                "exhausted_count"
            ),
            func.coalesce(
                func.sum(
                    AlertDelivery.attempt_count
                ),
                0,
            ).label(
                "total_attempt_count"
            ),
            func.min(
                case(
                    (
                        AlertDelivery.status
                        == ALERT_DELIVERY_STATUS_PENDING,
                        AlertDelivery.created_at,
                    ),
                    else_=None,
                )
            ).label(
                "oldest_pending_at"
            ),
            func.min(
                case(
                    (
                        AlertDelivery.status
                        == ALERT_DELIVERY_STATUS_RETRYABLE,
                        AlertDelivery.created_at,
                    ),
                    else_=None,
                )
            ).label(
                "oldest_retryable_at"
            ),
            func.min(
                case(
                    (
                        AlertDelivery.status
                        == ALERT_DELIVERY_STATUS_PROCESSING,
                        AlertDelivery.claimed_at,
                    ),
                    else_=None,
                )
            ).label(
                "oldest_processing_claimed_at"
            ),
        )
        .filter(
            AlertDelivery.organization_id
            == organization_id
        )
        .one()
    )

    return AlertDeliveryOperationalSummary(
        organization_id=organization_id,
        observed_at=effective_now,
        total_count=int(
            row.total_count or 0
        ),
        pending_count=int(
            row.pending_count or 0
        ),
        processing_count=int(
            row.processing_count or 0
        ),
        retryable_count=int(
            row.retryable_count or 0
        ),
        failed_count=int(
            row.failed_count or 0
        ),
        delivered_count=int(
            row.delivered_count or 0
        ),
        claimable_count=int(
            row.claimable_count or 0
        ),
        stale_processing_count=int(
            row.stale_processing_count or 0
        ),
        exhausted_count=int(
            row.exhausted_count or 0
        ),
        total_attempt_count=int(
            row.total_attempt_count or 0
        ),
        oldest_pending_at=(
            row.oldest_pending_at
        ),
        oldest_retryable_at=(
            row.oldest_retryable_at
        ),
        oldest_processing_claimed_at=(
            row.oldest_processing_claimed_at
        ),
    )