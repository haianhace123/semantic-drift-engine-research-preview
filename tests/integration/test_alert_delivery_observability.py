from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.models.alert import Alert
from src.models.alert_delivery import (
    ALERT_DELIVERY_DESTINATION_WEBHOOK,
    ALERT_DELIVERY_STATUS_DELIVERED,
    ALERT_DELIVERY_STATUS_FAILED,
    ALERT_DELIVERY_STATUS_PENDING,
    ALERT_DELIVERY_STATUS_PROCESSING,
    ALERT_DELIVERY_STATUS_RETRYABLE,
    AlertDelivery,
)
from src.models.organization import Organization
from src.services.alert_delivery_observability_service import (
    get_alert_delivery_operational_summary,
)
from src.services.alert_delivery_runtime_service import (
    DEFAULT_CLAIM_TIMEOUT_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
)


pytestmark = pytest.mark.skipif(
    os.getenv(
        "RUN_DB_INTEGRATION_TESTS"
    )
    != "1",
    reason=(
        "RUN_DB_INTEGRATION_TESTS is not enabled"
    ),
)


NOW = datetime(
    2026,
    9,
    13,
    5,
    0,
    tzinfo=timezone.utc,
)


def _create_organization(
    db,
    *,
    prefix: str,
) -> Organization:
    token = uuid.uuid4().hex

    organization = Organization(
        id=uuid.uuid4(),
        name=(
            f"Alert Delivery Observability "
            f"{prefix} {token[:8]}"
        ),
        slug=(
            f"alert-delivery-observability-"
            f"{prefix.lower()}-{token}"
        ),
        billing_email=(
            f"alert-delivery-observability-"
            f"{token[:16]}@example.com"
        ),
    )

    db.add(
        organization
    )
    db.flush()

    return organization


def _create_alert(
    db,
    *,
    organization_id: uuid.UUID,
    suffix: str,
) -> Alert:
    alert = Alert(
        id=uuid.uuid4(),
        organization_id=organization_id,
        source_type="pattern",
        source_id=uuid.uuid4(),
        detection_key=hashlib.sha256(
            (
                f"observability:"
                f"{organization_id}:"
                f"{suffix}:"
                f"{uuid.uuid4()}"
            ).encode(
                "utf-8"
            )
        ).hexdigest(),
        alert_type="pattern",
        title=(
            f"Observability {suffix}"
        ),
        message=(
            "Alert delivery observability "
            "integration test."
        ),
        severity="high",
        affected_entities=[],
        status="pending",
    )

    db.add(
        alert
    )
    db.flush()

    return alert


def _create_delivery(
    db,
    *,
    organization_id: uuid.UUID,
    alert_id: uuid.UUID,
    status: str,
    attempt_count: int,
    created_at: datetime,
    next_attempt_at: datetime | None = None,
    claimed_at: datetime | None = None,
    delivered_at: datetime | None = None,
) -> AlertDelivery:
    destination_id = uuid.uuid4()

    delivery_key = hashlib.sha256(
        (
            f"{alert_id}|"
            f"{ALERT_DELIVERY_DESTINATION_WEBHOOK}|"
            f"{destination_id}"
        ).encode(
            "utf-8"
        )
    ).hexdigest()

    delivery = AlertDelivery(
        id=uuid.uuid4(),
        organization_id=organization_id,
        alert_id=alert_id,
        destination_type=(
            ALERT_DELIVERY_DESTINATION_WEBHOOK
        ),
        destination_id=destination_id,
        delivery_key=delivery_key,
        status=status,
        payload={
            "alert_id": str(
                alert_id
            ),
            "type": "pattern",
        },
        attempt_count=attempt_count,
        next_attempt_at=next_attempt_at,
        claimed_at=claimed_at,
        delivered_at=delivered_at,
        created_at=created_at,
    )

    db.add(
        delivery
    )
    db.flush()

    return delivery


def _cleanup(
    organization_ids: list[uuid.UUID],
) -> None:
    if not organization_ids:
        return

    db = SessionLocal()

    try:
        db.execute(
            delete(
                AlertDelivery
            ).where(
                AlertDelivery.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(
                Alert
            ).where(
                Alert.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(
                Organization
            ).where(
                Organization.id.in_(
                    organization_ids
                )
            )
        )

        db.commit()

    finally:
        db.close()


def test_operational_summary_matches_runtime_queue_semantics():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _create_organization(
            db,
            prefix="Primary",
        )
        foreign_organization = (
            _create_organization(
                db,
                prefix="Foreign",
            )
        )

        organization_ids.extend(
            [
                organization.id,
                foreign_organization.id,
            ]
        )

        alert = _create_alert(
            db,
            organization_id=(
                organization.id
            ),
            suffix="primary",
        )

        foreign_alert = _create_alert(
            db,
            organization_id=(
                foreign_organization.id
            ),
            suffix="foreign",
        )

        oldest_pending_at = (
            NOW
            - timedelta(
                hours=3
            )
        )

        oldest_retryable_at = (
            NOW
            - timedelta(
                hours=2
            )
        )

        stale_claimed_at = (
            NOW
            - timedelta(
                seconds=(
                    DEFAULT_CLAIM_TIMEOUT_SECONDS
                    + 1
                )
            )
        )

        fresh_claimed_at = (
            NOW
            - timedelta(
                seconds=30
            )
        )

        _create_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_PENDING
            ),
            attempt_count=0,
            created_at=oldest_pending_at,
        )

        _create_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_PENDING
            ),
            attempt_count=(
                DEFAULT_MAX_ATTEMPTS
            ),
            created_at=(
                NOW
                - timedelta(
                    hours=1
                )
            ),
        )

        _create_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_RETRYABLE
            ),
            attempt_count=1,
            created_at=oldest_retryable_at,
            next_attempt_at=(
                NOW
                - timedelta(
                    seconds=1
                )
            ),
        )

        _create_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_RETRYABLE
            ),
            attempt_count=2,
            created_at=(
                NOW
                - timedelta(
                    minutes=50
                )
            ),
            next_attempt_at=(
                NOW
                + timedelta(
                    minutes=10
                )
            ),
        )

        _create_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_RETRYABLE
            ),
            attempt_count=2,
            created_at=(
                NOW
                - timedelta(
                    minutes=40
                )
            ),
            next_attempt_at=None,
        )

        _create_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_PROCESSING
            ),
            attempt_count=1,
            created_at=(
                NOW
                - timedelta(
                    minutes=30
                )
            ),
            claimed_at=fresh_claimed_at,
        )

        _create_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_PROCESSING
            ),
            attempt_count=2,
            created_at=(
                NOW
                - timedelta(
                    minutes=25
                )
            ),
            claimed_at=stale_claimed_at,
        )

        _create_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_PROCESSING
            ),
            attempt_count=3,
            created_at=(
                NOW
                - timedelta(
                    minutes=20
                )
            ),
            claimed_at=None,
        )

        _create_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_PROCESSING
            ),
            attempt_count=(
                DEFAULT_MAX_ATTEMPTS
            ),
            created_at=(
                NOW
                - timedelta(
                    minutes=15
                )
            ),
            claimed_at=stale_claimed_at,
        )

        _create_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_FAILED
            ),
            attempt_count=(
                DEFAULT_MAX_ATTEMPTS
            ),
            created_at=(
                NOW
                - timedelta(
                    minutes=10
                )
            ),
        )

        _create_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_DELIVERED
            ),
            attempt_count=1,
            created_at=(
                NOW
                - timedelta(
                    minutes=5
                )
            ),
            delivered_at=(
                NOW
                - timedelta(
                    minutes=4
                )
            ),
        )

        _create_delivery(
            db,
            organization_id=(
                foreign_organization.id
            ),
            alert_id=foreign_alert.id,
            status=(
                ALERT_DELIVERY_STATUS_PENDING
            ),
            attempt_count=0,
            created_at=(
                NOW
                - timedelta(
                    days=1
                )
            ),
        )

        db.commit()

        summary = (
            get_alert_delivery_operational_summary(
                db,
                organization_id=(
                    organization.id
                ),
                now=NOW,
            )
        )

        assert (
            summary.organization_id
            == organization.id
        )
        assert summary.observed_at == NOW

        assert summary.total_count == 11

        assert summary.pending_count == 2
        assert summary.processing_count == 4
        assert summary.retryable_count == 3
        assert summary.failed_count == 1
        assert summary.delivered_count == 1

        assert summary.claimable_count == 5
        assert (
            summary.stale_processing_count
            == 3
        )
        assert summary.exhausted_count == 2

        assert (
            summary.total_attempt_count
            == 27
        )

        assert (
            summary.oldest_pending_at
            == oldest_pending_at
        )
        assert (
            summary.oldest_retryable_at
            == oldest_retryable_at
        )
        assert (
            summary.oldest_processing_claimed_at
            == stale_claimed_at
        )

    finally:
        db.close()

        _cleanup(
            organization_ids
        )


def test_operational_summary_returns_zeroes_for_empty_tenant():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _create_organization(
            db,
            prefix="Empty",
        )

        organization_ids.append(
            organization.id
        )

        db.commit()

        summary = (
            get_alert_delivery_operational_summary(
                db,
                organization_id=(
                    organization.id
                ),
                now=NOW,
            )
        )

        assert summary.total_count == 0

        assert summary.pending_count == 0
        assert summary.processing_count == 0
        assert summary.retryable_count == 0
        assert summary.failed_count == 0
        assert summary.delivered_count == 0

        assert summary.claimable_count == 0
        assert (
            summary.stale_processing_count
            == 0
        )
        assert summary.exhausted_count == 0

        assert (
            summary.total_attempt_count
            == 0
        )

        assert (
            summary.oldest_pending_at
            is None
        )
        assert (
            summary.oldest_retryable_at
            is None
        )
        assert (
            summary.oldest_processing_claimed_at
            is None
        )

    finally:
        db.close()

        _cleanup(
            organization_ids
        )