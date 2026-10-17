from __future__ import annotations

import os
import threading
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
from src.models.webhook_subscription import WebhookSubscription
from src.services.alert_delivery_service import (
    persist_alert_delivery,
)
from src.services.alert_delivery_runtime_service import (
    AlertDeliveryRuntimeError,
    claim_alert_deliveries,
    finalize_exhausted_alert_deliveries,
    mark_delivery_delivered,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(
    db,
    *,
    prefix: str,
) -> Organization:
    suffix = uuid.uuid4().hex[:12]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"alert-runtime-{prefix}-{suffix}",
        slug=f"alert-runtime-{prefix}-{suffix}",
        billing_email=(
            f"alert-runtime-{prefix}-{suffix}@example.com"
        ),
    )

    db.add(organization)
    db.flush()

    return organization


def _make_alert(
    db,
    *,
    organization_id: uuid.UUID,
) -> Alert:
    alert = Alert(
        id=uuid.uuid4(),
        organization_id=organization_id,
        event_id=None,
        source_type="risk",
        source_id=uuid.uuid4(),
        detection_key=uuid.uuid4().hex,
        alert_type="risk",
        title="Risk increased",
        message="Material semantic risk detected.",
        severity="high",
        recommendation="Review the affected source.",
        status="pending",
        affected_entities=[],
        sent_to=[],
    )

    db.add(alert)
    db.flush()

    return alert


def _make_webhook(
    db,
    *,
    organization_id: uuid.UUID,
) -> WebhookSubscription:
    subscription = WebhookSubscription(
        id=uuid.uuid4(),
        organization_id=organization_id,
        url="https://example.test/webhook",
        events=["high_risk"],
        secret_ciphertext=b"integration-test-ciphertext",
        secret_nonce=b"0123456789ab",
        secret_encryption_version=1,
        is_active=True,
    )

    db.add(subscription)
    db.flush()

    return subscription


def _make_delivery(
    db,
    *,
    organization_id: uuid.UUID,
    alert_id: uuid.UUID,
    webhook_id: uuid.UUID,
) -> AlertDelivery:
    result = persist_alert_delivery(
        db,
        organization_id=organization_id,
        alert_id=alert_id,
        destination_type=(
            ALERT_DELIVERY_DESTINATION_WEBHOOK
        ),
        destination_id=webhook_id,
    )

    return result.delivery


def _cleanup(
    organization_ids: list[uuid.UUID],
) -> None:
    if not organization_ids:
        return

    db = SessionLocal()

    try:
        db.execute(
            delete(AlertDelivery).where(
                AlertDelivery.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(WebhookSubscription).where(
                WebhookSubscription.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(Alert).where(
                Alert.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(Organization).where(
                Organization.id.in_(
                    organization_ids
                )
            )
        )

        db.commit()

    finally:
        db.close()


def test_claim_selects_only_ready_delivery_states():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db,
            prefix="ready-states",
        )
        organization_ids.append(organization.id)

        alert = _make_alert(
            db,
            organization_id=organization.id,
        )

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
        )

        now = datetime(
            2026,
            9,
            12,
            1,
            0,
            tzinfo=timezone.utc,
        )

        pending = _make_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            webhook_id=webhook.id,
        )

        due_retry = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_RETRYABLE,
            payload={"event": "due-retry"},
            attempt_count=1,
            next_attempt_at=now - timedelta(seconds=1),
        )

        future_retry = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_RETRYABLE,
            payload={"event": "future-retry"},
            attempt_count=1,
            next_attempt_at=now + timedelta(minutes=10),
        )

        stale_processing = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_PROCESSING,
            payload={"event": "stale-processing"},
            attempt_count=2,
            claimed_at=now - timedelta(minutes=10),
        )

        fresh_processing = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_PROCESSING,
            payload={"event": "fresh-processing"},
            attempt_count=2,
            claimed_at=now - timedelta(seconds=30),
        )

        delivered = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_DELIVERED,
            payload={"event": "delivered"},
            attempt_count=1,
            delivered_at=now - timedelta(minutes=1),
        )

        failed = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_FAILED,
            payload={"event": "failed"},
            attempt_count=5,
            last_error="terminal failure",
        )

        db.add_all(
            [
                due_retry,
                future_retry,
                stale_processing,
                fresh_processing,
                delivered,
                failed,
            ]
        )

        db.commit()

        pending_id = pending.id
        due_retry_id = due_retry.id
        stale_processing_id = stale_processing.id
        future_retry_id = future_retry.id
        fresh_processing_id = fresh_processing.id
        delivered_id = delivered.id
        failed_id = failed.id

        claimed = claim_alert_deliveries(
            db,
            limit=20,
            now=now,
            claim_timeout_seconds=300,
        )

        claimed_ids = {
            delivery.id
            for delivery in claimed
        }

        assert claimed_ids == {
            pending_id,
            due_retry_id,
            stale_processing_id,
        }

        assert (
            future_retry_id
            not in claimed_ids
        )
        assert (
            fresh_processing_id
            not in claimed_ids
        )
        assert delivered_id not in claimed_ids
        assert failed_id not in claimed_ids

        db.commit()

        rows = {
            delivery.id: delivery
            for delivery in (
                db.query(AlertDelivery)
                .filter(
                    AlertDelivery.organization_id
                    == organization.id
                )
                .all()
            )
        }

        assert (
            rows[pending_id].status
            == ALERT_DELIVERY_STATUS_PROCESSING
        )
        assert rows[pending_id].attempt_count == 1
        assert rows[pending_id].claimed_at == now

        assert (
            rows[due_retry_id].status
            == ALERT_DELIVERY_STATUS_PROCESSING
        )
        assert rows[due_retry_id].attempt_count == 2
        assert rows[due_retry_id].next_attempt_at is None
        assert rows[due_retry_id].claimed_at == now

        assert (
            rows[stale_processing_id].status
            == ALERT_DELIVERY_STATUS_PROCESSING
        )
        assert (
            rows[stale_processing_id].attempt_count
            == 3
        )
        assert (
            rows[stale_processing_id].claimed_at
            == now
        )

        assert (
            rows[future_retry_id].status
            == ALERT_DELIVERY_STATUS_RETRYABLE
        )
        assert (
            rows[fresh_processing_id].status
            == ALERT_DELIVERY_STATUS_PROCESSING
        )
        assert (
            rows[delivered_id].status
            == ALERT_DELIVERY_STATUS_DELIVERED
        )
        assert (
            rows[failed_id].status
            == ALERT_DELIVERY_STATUS_FAILED
        )

    finally:
        db.close()
        _cleanup(organization_ids)


def test_claim_recovers_processing_without_claim_timestamp():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db,
            prefix="missing-claim-time",
        )
        organization_ids.append(organization.id)

        alert = _make_alert(
            db,
            organization_id=organization.id,
        )

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
        )

        delivery = _make_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            webhook_id=webhook.id,
        )

        delivery.status = ALERT_DELIVERY_STATUS_PROCESSING
        delivery.attempt_count = 1
        delivery.claimed_at = None

        db.commit()

        delivery_id = delivery.id

        now = datetime(
            2026,
            9,
            12,
            1,
            10,
            tzinfo=timezone.utc,
        )

        claimed = claim_alert_deliveries(
            db,
            limit=1,
            now=now,
        )

        assert len(claimed) == 1
        assert claimed[0].id == delivery_id
        assert claimed[0].attempt_count == 2
        assert claimed[0].claimed_at == now

        db.commit()

    finally:
        db.close()
        _cleanup(organization_ids)


def test_claim_respects_limit_and_deterministic_order():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db,
            prefix="limit-order",
        )
        organization_ids.append(organization.id)

        alert = _make_alert(
            db,
            organization_id=organization.id,
        )

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
        )

        first = _make_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            webhook_id=webhook.id,
        )

        second = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_PENDING,
            payload={"event": "second"},
            attempt_count=0,
        )

        db.add(second)
        db.flush()

        created_at = datetime(
            2026,
            9,
            12,
            0,
            30,
            tzinfo=timezone.utc,
        )

        first.created_at = created_at
        second.created_at = created_at

        db.commit()

        expected_first_id = min(
            first.id,
            second.id,
        )

        claimed = claim_alert_deliveries(
            db,
            limit=1,
            now=datetime(
                2026,
                9,
                12,
                1,
                0,
                tzinfo=timezone.utc,
            ),
        )

        assert len(claimed) == 1
        assert claimed[0].id == expected_first_id

        db.commit()

    finally:
        db.close()
        _cleanup(organization_ids)

def test_claim_does_not_reclaim_exhausted_deliveries():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db,
            prefix="exhausted",
        )
        organization_ids.append(organization.id)

        alert = _make_alert(
            db,
            organization_id=organization.id,
        )

        now = datetime(
            2026,
            9,
            12,
            1,
            15,
            tzinfo=timezone.utc,
        )

        pending = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_PENDING,
            payload={"event": "exhausted-pending"},
            attempt_count=5,
        )

        retryable = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_RETRYABLE,
            payload={"event": "exhausted-retryable"},
            attempt_count=5,
            next_attempt_at=now - timedelta(seconds=1),
        )

        stale_processing = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_PROCESSING,
            payload={"event": "exhausted-processing"},
            attempt_count=5,
            claimed_at=now - timedelta(minutes=10),
        )

        db.add_all(
            [
                pending,
                retryable,
                stale_processing,
            ]
        )

        db.commit()

        claimed = claim_alert_deliveries(
            db,
            limit=10,
            now=now,
            max_attempts=5,
            claim_timeout_seconds=300,
        )

        assert claimed == ()

        db.rollback()

        rows = (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.organization_id
                == organization.id
            )
            .all()
        )

        assert len(rows) == 3

        assert {
            row.status
            for row in rows
        } == {
            ALERT_DELIVERY_STATUS_PENDING,
            ALERT_DELIVERY_STATUS_RETRYABLE,
            ALERT_DELIVERY_STATUS_PROCESSING,
        }

        assert all(
            row.attempt_count == 5
            for row in rows
        )

    finally:
        db.close()
        _cleanup(organization_ids)

def test_finalize_exhausted_deliveries_preserves_fresh_final_attempt():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db,
            prefix="finalize-exhausted",
        )
        organization_ids.append(organization.id)

        alert = _make_alert(
            db,
            organization_id=organization.id,
        )

        now = datetime(
            2026,
            9,
            12,
            1,
            30,
            tzinfo=timezone.utc,
        )

        exhausted_pending = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_PENDING,
            payload={
                "event": "exhausted-pending",
            },
            attempt_count=5,
        )

        exhausted_retryable = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_RETRYABLE,
            payload={
                "event": "exhausted-retryable",
            },
            attempt_count=5,
            next_attempt_at=(
                now - timedelta(seconds=1)
            ),
            last_error="temporary failure",
        )

        exhausted_stale_processing = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_PROCESSING,
            payload={
                "event": "exhausted-stale",
            },
            attempt_count=5,
            claimed_at=(
                now - timedelta(minutes=10)
            ),
        )

        exhausted_fresh_processing = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_PROCESSING,
            payload={
                "event": "exhausted-fresh",
            },
            attempt_count=5,
            claimed_at=(
                now - timedelta(seconds=30)
            ),
        )

        below_limit_retryable = AlertDelivery(
            id=uuid.uuid4(),
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=uuid.uuid4(),
            delivery_key=uuid.uuid4().hex,
            status=ALERT_DELIVERY_STATUS_RETRYABLE,
            payload={
                "event": "below-limit",
            },
            attempt_count=4,
            next_attempt_at=(
                now - timedelta(seconds=1)
            ),
        )

        db.add_all(
            [
                exhausted_pending,
                exhausted_retryable,
                exhausted_stale_processing,
                exhausted_fresh_processing,
                below_limit_retryable,
            ]
        )

        db.commit()

        pending_id = exhausted_pending.id
        retryable_id = exhausted_retryable.id
        stale_id = exhausted_stale_processing.id
        fresh_id = exhausted_fresh_processing.id
        below_limit_id = below_limit_retryable.id

        finalized = finalize_exhausted_alert_deliveries(
            db,
            limit=20,
            now=now,
            max_attempts=5,
            claim_timeout_seconds=300,
        )

        finalized_ids = {
            delivery.id
            for delivery in finalized
        }

        assert finalized_ids == {
            pending_id,
            retryable_id,
            stale_id,
        }

        db.commit()
        db.expire_all()

        rows = {
            delivery.id: delivery
            for delivery in (
                db.query(AlertDelivery)
                .filter(
                    AlertDelivery.organization_id
                    == organization.id
                )
                .all()
            )
        }

        assert (
            rows[pending_id].status
            == ALERT_DELIVERY_STATUS_FAILED
        )
        assert rows[pending_id].attempt_count == 5
        assert rows[pending_id].claimed_at is None
        assert rows[pending_id].next_attempt_at is None
        assert (
            rows[pending_id].last_error
            == "Maximum delivery attempts exhausted."
        )

        assert (
            rows[retryable_id].status
            == ALERT_DELIVERY_STATUS_FAILED
        )
        assert rows[retryable_id].attempt_count == 5
        assert rows[retryable_id].claimed_at is None
        assert rows[retryable_id].next_attempt_at is None

        # Preserve the most useful previously recorded provider error.
        assert (
            rows[retryable_id].last_error
            == "temporary failure"
        )

        assert (
            rows[stale_id].status
            == ALERT_DELIVERY_STATUS_FAILED
        )
        assert rows[stale_id].attempt_count == 5
        assert rows[stale_id].claimed_at is None
        assert rows[stale_id].next_attempt_at is None
        assert (
            rows[stale_id].last_error
            == "Maximum delivery attempts exhausted."
        )

        # The current worker still owns this final allowed attempt.
        assert (
            rows[fresh_id].status
            == ALERT_DELIVERY_STATUS_PROCESSING
        )
        assert rows[fresh_id].attempt_count == 5
        assert (
            rows[fresh_id].claimed_at
            == now - timedelta(seconds=30)
        )

        # A delivery below the attempt ceiling is not terminalized.
        assert (
            rows[below_limit_id].status
            == ALERT_DELIVERY_STATUS_RETRYABLE
        )
        assert rows[below_limit_id].attempt_count == 4

    finally:
        db.close()
        _cleanup(organization_ids)

def test_stale_worker_cannot_complete_newer_claim():
    setup_db = SessionLocal()
    first_worker = None
    second_worker = None
    stale_result_db = None
    verify_db = None
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            setup_db,
            prefix="attempt-fencing",
        )
        organization_ids.append(organization.id)

        alert = _make_alert(
            setup_db,
            organization_id=organization.id,
        )

        webhook = _make_webhook(
            setup_db,
            organization_id=organization.id,
        )

        delivery = _make_delivery(
            setup_db,
            organization_id=organization.id,
            alert_id=alert.id,
            webhook_id=webhook.id,
        )

        setup_db.commit()

        delivery_id = delivery.id
        setup_db.close()

        first_worker = SessionLocal()

        first_claim_time = datetime(
            2026,
            9,
            12,
            1,
            0,
            tzinfo=timezone.utc,
        )

        first_claim = claim_alert_deliveries(
            first_worker,
            limit=1,
            now=first_claim_time,
            claim_timeout_seconds=300,
        )

        assert len(first_claim) == 1
        assert first_claim[0].id == delivery_id
        assert first_claim[0].attempt_count == 1

        first_attempt_count = first_claim[0].attempt_count

        first_worker.commit()
        first_worker.close()
        first_worker = None

        second_worker = SessionLocal()

        second_claim_time = (
            first_claim_time + timedelta(seconds=301)
        )

        second_claim = claim_alert_deliveries(
            second_worker,
            limit=1,
            now=second_claim_time,
            claim_timeout_seconds=300,
        )

        assert len(second_claim) == 1
        assert second_claim[0].id == delivery_id
        assert second_claim[0].attempt_count == 2

        second_attempt_count = second_claim[0].attempt_count

        second_worker.commit()
        second_worker.close()
        second_worker = None

        assert first_attempt_count == 1
        assert second_attempt_count == 2

        stale_result_db = SessionLocal()

        with pytest.raises(
            AlertDeliveryRuntimeError,
            match="no longer current",
        ):
            mark_delivery_delivered(
                stale_result_db,
                delivery_id=delivery_id,
                expected_attempt_count=(
                    first_attempt_count
                ),
                delivered_at=(
                    second_claim_time
                    + timedelta(seconds=1)
                ),
            )

        stale_result_db.rollback()
        stale_result_db.close()
        stale_result_db = None

        verify_db = SessionLocal()

        persisted = (
            verify_db.query(AlertDelivery)
            .filter(
                AlertDelivery.id == delivery_id
            )
            .one()
        )

        assert (
            persisted.status
            == ALERT_DELIVERY_STATUS_PROCESSING
        )
        assert persisted.attempt_count == 2
        assert persisted.claimed_at == second_claim_time
        assert persisted.delivered_at is None

    finally:
        if verify_db is not None:
            verify_db.close()

        if stale_result_db is not None:
            stale_result_db.close()

        if second_worker is not None:
            second_worker.close()

        if first_worker is not None:
            first_worker.close()

        if setup_db.is_active:
            setup_db.close()

        _cleanup(organization_ids)


def test_result_transition_blocks_reclaim_until_commit():
    setup_db = SessionLocal()
    stale_worker = None
    reclaim_worker = None
    verify_db = None
    organization_ids: list[uuid.UUID] = []

    stale_loaded = threading.Event()
    allow_stale_flush = threading.Event()
    stale_completed = threading.Event()

    stale_errors: list[BaseException] = []

    try:
        organization = _make_organization(
            setup_db,
            prefix="result-reclaim-race",
        )
        organization_ids.append(organization.id)

        alert = _make_alert(
            setup_db,
            organization_id=organization.id,
        )

        webhook = _make_webhook(
            setup_db,
            organization_id=organization.id,
        )

        delivery = _make_delivery(
            setup_db,
            organization_id=organization.id,
            alert_id=alert.id,
            webhook_id=webhook.id,
        )

        first_claim_time = datetime(
            2026,
            9,
            12,
            1,
            0,
            tzinfo=timezone.utc,
        )

        delivery.status = ALERT_DELIVERY_STATUS_PROCESSING
        delivery.attempt_count = 1
        delivery.claimed_at = first_claim_time

        setup_db.commit()

        delivery_id = delivery.id
        setup_db.close()

        def complete_stale_attempt() -> None:
            nonlocal stale_worker

            stale_worker = SessionLocal()

            try:
                loaded = (
                    stale_worker.query(AlertDelivery)
                    .filter(
                        AlertDelivery.id == delivery_id
                    )
                    .one()
                )

                assert (
                    loaded.status
                    == ALERT_DELIVERY_STATUS_PROCESSING
                )
                assert loaded.attempt_count == 1

                stale_loaded.set()

                assert allow_stale_flush.wait(timeout=10)

                mark_delivery_delivered(
                    stale_worker,
                    delivery_id=delivery_id,
                    expected_attempt_count=1,
                    delivered_at=(
                        first_claim_time
                        + timedelta(seconds=302)
                    ),
                )

                stale_worker.commit()

            except BaseException as exc:
                stale_errors.append(exc)
                stale_worker.rollback()

            finally:
                stale_completed.set()
                stale_worker.close()
                stale_worker = None

        thread = threading.Thread(
            target=complete_stale_attempt,
        )
        thread.start()

        assert stale_loaded.wait(timeout=10)

        reclaim_worker = SessionLocal()

        reclaimed = claim_alert_deliveries(
            reclaim_worker,
            limit=1,
            now=(
                first_claim_time
                + timedelta(seconds=301)
            ),
            claim_timeout_seconds=300,
        )

        assert len(reclaimed) == 1
        assert reclaimed[0].id == delivery_id
        assert reclaimed[0].attempt_count == 2

        reclaim_worker.commit()

        allow_stale_flush.set()

        thread.join(timeout=10)

        assert not thread.is_alive()
        assert stale_completed.is_set()

        verify_db = SessionLocal()

        persisted = (
            verify_db.query(AlertDelivery)
            .filter(
                AlertDelivery.id == delivery_id
            )
            .one()
        )

        assert stale_errors
        assert isinstance(
            stale_errors[0],
            AlertDeliveryRuntimeError,
        )

        assert (
            persisted.status
            == ALERT_DELIVERY_STATUS_PROCESSING
        )
        assert persisted.attempt_count == 2
        assert persisted.claimed_at == (
            first_claim_time
            + timedelta(seconds=301)
        )
        assert persisted.delivered_at is None

    finally:
        allow_stale_flush.set()

        if verify_db is not None:
            verify_db.close()

        if reclaim_worker is not None:
            reclaim_worker.close()

        if stale_worker is not None:
            stale_worker.close()

        if setup_db.is_active:
            setup_db.close()

        _cleanup(organization_ids)


def test_reclaim_skips_result_transition_row_lock():
    setup_db = SessionLocal()
    result_worker = None
    reclaim_worker = None
    verify_db = None
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            setup_db,
            prefix="result-lock-reclaim",
        )
        organization_ids.append(organization.id)

        alert = _make_alert(
            setup_db,
            organization_id=organization.id,
        )

        webhook = _make_webhook(
            setup_db,
            organization_id=organization.id,
        )

        delivery = _make_delivery(
            setup_db,
            organization_id=organization.id,
            alert_id=alert.id,
            webhook_id=webhook.id,
        )

        first_claim_time = datetime(
            2026,
            9,
            12,
            1,
            0,
            tzinfo=timezone.utc,
        )

        delivery.status = ALERT_DELIVERY_STATUS_PROCESSING
        delivery.attempt_count = 1
        delivery.claimed_at = first_claim_time

        setup_db.commit()

        delivery_id = delivery.id
        setup_db.close()

        result_worker = SessionLocal()

        mark_delivery_delivered(
            result_worker,
            delivery_id=delivery_id,
            expected_attempt_count=1,
            delivered_at=(
                first_claim_time
                + timedelta(seconds=302)
            ),
        )

        # result_worker intentionally does not commit yet. The result
        # transition must retain the row lock until its transaction ends.
        reclaim_worker = SessionLocal()

        reclaimed = claim_alert_deliveries(
            reclaim_worker,
            limit=1,
            now=(
                first_claim_time
                + timedelta(seconds=301)
            ),
            claim_timeout_seconds=300,
        )

        assert reclaimed == ()

        reclaim_worker.rollback()
        result_worker.commit()

        verify_db = SessionLocal()

        persisted = (
            verify_db.query(AlertDelivery)
            .filter(
                AlertDelivery.id == delivery_id
            )
            .one()
        )

        assert (
            persisted.status
            == ALERT_DELIVERY_STATUS_DELIVERED
        )
        assert persisted.attempt_count == 1
        assert persisted.claimed_at is None
        assert persisted.delivered_at == (
            first_claim_time
            + timedelta(seconds=302)
        )

    finally:
        if verify_db is not None:
            verify_db.close()

        if reclaim_worker is not None:
            reclaim_worker.close()

        if result_worker is not None:
            result_worker.close()

        if setup_db.is_active:
            setup_db.close()

        _cleanup(organization_ids)


def test_skip_locked_prevents_double_claim():
    setup_db = SessionLocal()
    worker_a = None
    worker_b = None
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            setup_db,
            prefix="skip-locked",
        )
        organization_ids.append(organization.id)

        alert = _make_alert(
            setup_db,
            organization_id=organization.id,
        )

        webhook = _make_webhook(
            setup_db,
            organization_id=organization.id,
        )

        delivery = _make_delivery(
            setup_db,
            organization_id=organization.id,
            alert_id=alert.id,
            webhook_id=webhook.id,
        )

        setup_db.commit()

        delivery_id = delivery.id
        setup_db.close()

        worker_a = SessionLocal()
        worker_b = SessionLocal()

        now = datetime(
            2026,
            9,
            12,
            1,
            20,
            tzinfo=timezone.utc,
        )

        claimed_a = claim_alert_deliveries(
            worker_a,
            limit=1,
            now=now,
        )

        assert len(claimed_a) == 1
        assert claimed_a[0].id == delivery_id

        # worker_a intentionally does not commit yet. Its row lock
        # remains active while worker_b attempts another claim.
        claimed_b = claim_alert_deliveries(
            worker_b,
            limit=1,
            now=now,
        )

        assert claimed_b == ()

        worker_b.rollback()
        worker_a.commit()

        persisted = (
            worker_b.query(AlertDelivery)
            .filter(
                AlertDelivery.id == delivery_id
            )
            .one()
        )

        assert (
            persisted.status
            == ALERT_DELIVERY_STATUS_PROCESSING
        )
        assert persisted.attempt_count == 1
        assert persisted.claimed_at == now

    finally:
        if worker_b is not None:
            worker_b.close()

        if worker_a is not None:
            worker_a.close()

        if setup_db.is_active:
            setup_db.close()

        _cleanup(organization_ids)