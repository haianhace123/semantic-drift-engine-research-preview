from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import uuid
from unittest.mock import MagicMock
import base64

import httpx
import pytest
from sqlalchemy import delete

import scripts.alert_delivery_worker as worker_module
import src.services.webhook_delivery_transport_service as webhook_transport
from scripts.alert_delivery_worker import (
    AlertDeliveryWorker,
)
from src.core.database import SessionLocal
from src.models.alert import Alert
from src.models.alert_delivery import (
    ALERT_DELIVERY_DESTINATION_WEBHOOK,
    ALERT_DELIVERY_STATUS_DELIVERED,
    ALERT_DELIVERY_STATUS_RETRYABLE,
    AlertDelivery,
)
from src.models.organization import Organization
from src.models.webhook_subscription import WebhookSubscription
from src.services.alert_delivery_service import (
    persist_alert_delivery,
)
from src.services.webhook_delivery_transport_service import (
    HEADER_DELIVERY_ID,
    HEADER_DELIVERY_KEY,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    TransientWebhookDeliveryError,
    WebhookDeliveryResponse,
)
from src.core.config import settings
from src.services.webhook_secret_encryption_service import (
    encrypt_webhook_secret,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _encryption_key() -> str:
    return base64.urlsafe_b64encode(
        b"\x42" * 32
    ).decode("ascii")


def _make_organization(
    db,
    *,
    prefix: str,
) -> Organization:
    suffix = uuid.uuid4().hex[:12]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"alert-worker-{prefix}-{suffix}",
        slug=f"alert-worker-{prefix}-{suffix}",
        billing_email=(
            f"alert-worker-{prefix}-{suffix}@example.com"
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
    encryption_key: str,
    signing_secret: str | None = None,
) -> WebhookSubscription:
    subscription_id = uuid.uuid4()
    plaintext_secret = (
        signing_secret
        if signing_secret is not None
        else uuid.uuid4().hex
    )

    encrypted = encrypt_webhook_secret(
        plaintext_secret,
        organization_id=organization_id,
        subscription_id=subscription_id,
        encoded_encryption_key=encryption_key,
    )

    subscription = WebhookSubscription(
        id=subscription_id,
        organization_id=organization_id,
        url="https://example.test/webhook",
        events=["high_risk"],
        secret_ciphertext=encrypted.ciphertext,
        secret_nonce=encrypted.nonce,
        secret_encryption_version=encrypted.version,
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


def _worker_without_http_init() -> AlertDeliveryWorker:
    worker = AlertDeliveryWorker.__new__(
        AlertDeliveryWorker
    )
    worker._stopping = False

    return worker


def test_worker_delivers_pending_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encryption_key = _encryption_key()
    setup_db = SessionLocal()
    verify_db = None
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            setup_db,
            prefix="delivered",
        )
        organization_ids.append(organization.id)

        alert = _make_alert(
            setup_db,
            organization_id=organization.id,
        )

        webhook = _make_webhook(
            setup_db,
            organization_id=organization.id,
            encryption_key=encryption_key,
        )

        delivery = _make_delivery(
            setup_db,
            organization_id=organization.id,
            alert_id=alert.id,
            webhook_id=webhook.id,
        )

        delivery_id = delivery.id
        expected_payload = dict(delivery.payload)

        setup_db.commit()
        setup_db.close()

        transport = MagicMock(
            return_value=WebhookDeliveryResponse(
                status_code=204,
            )
        )

        monkeypatch.setattr(
            worker_module,
            "deliver_webhook",
            transport,
        )

        worker = _worker_without_http_init()

        monkeypatch.setattr(
            settings,
            "WEBHOOK_SECRET_ENCRYPTION_KEY",
            encryption_key,
        )

        processed = worker.run_once()

        assert processed == 1

        transport.assert_called_once()

        call = transport.call_args

        assert call.kwargs["url"] == (
            "https://example.test/webhook"
        )
        assert (
            call.kwargs["delivery_id"]
            == str(delivery_id)
        )
        assert (
            call.kwargs["payload"]
            == expected_payload
        )

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
        assert persisted.next_attempt_at is None
        assert persisted.last_attempt_at is not None
        assert persisted.delivered_at is not None
        assert persisted.last_error is None

    finally:
        if verify_db is not None:
            verify_db.close()

        if setup_db.is_active:
            setup_db.close()

        _cleanup(organization_ids)


def test_worker_delivers_with_encrypted_webhook_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_db = SessionLocal()
    verify_db = None
    organization_ids: list[uuid.UUID] = []

    encryption_key = _encryption_key()

    try:
        organization = _make_organization(
            setup_db,
            prefix="encrypted-secret",
        )
        organization_ids.append(organization.id)

        alert = _make_alert(
            setup_db,
            organization_id=organization.id,
        )

        webhook = _make_webhook(
            setup_db,
            organization_id=organization.id,
            encryption_key=encryption_key,
            signing_secret="encrypted-worker-secret",
        )

        delivery = _make_delivery(
            setup_db,
            organization_id=organization.id,
            alert_id=alert.id,
            webhook_id=webhook.id,
        )

        delivery_id = delivery.id

        setup_db.commit()
        setup_db.close()

        monkeypatch.setattr(
            settings,
            "WEBHOOK_SECRET_ENCRYPTION_KEY",
            encryption_key,
        )

        transport = MagicMock(
            return_value=WebhookDeliveryResponse(
                status_code=204,
            )
        )

        monkeypatch.setattr(
            worker_module,
            "deliver_webhook",
            transport,
        )

        worker = _worker_without_http_init()

        monkeypatch.setattr(
            settings,
            "WEBHOOK_SECRET_ENCRYPTION_KEY",
            encryption_key,
        )

        processed = worker.run_once()

        assert processed == 1

        transport.assert_called_once()

        assert (
            transport.call_args.kwargs["secret_key"]
            == "encrypted-worker-secret"
        )

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
        assert persisted.last_attempt_at is not None
        assert persisted.delivered_at is not None
        assert persisted.last_error is None

    finally:
        if verify_db is not None:
            verify_db.close()

        if setup_db.is_active:
            setup_db.close()

        _cleanup(organization_ids)


def test_worker_delivers_through_real_webhook_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encryption_key = _encryption_key()
    signing_secret = "real-transport-signing-secret"
    setup_db = SessionLocal()
    verify_db = None
    organization_ids: list[uuid.UUID] = []
    worker = None

    received_requests: list[httpx.Request] = []

    try:
        organization = _make_organization(
            setup_db,
            prefix="real-transport",
        )
        organization_ids.append(
            organization.id
        )

        alert = _make_alert(
            setup_db,
            organization_id=organization.id,
        )

        webhook = _make_webhook(
            setup_db,
            organization_id=organization.id,
            encryption_key=encryption_key,
            signing_secret=signing_secret,
        )

        delivery = _make_delivery(
            setup_db,
            organization_id=organization.id,
            alert_id=alert.id,
            webhook_id=webhook.id,
        )

        delivery_id = delivery.id
        delivery_key = delivery.delivery_key
        expected_payload = dict(
            delivery.payload
        )

        setup_db.commit()
        setup_db.close()

        def fake_getaddrinfo(
            host,
            port,
            *,
            type,
        ):
            assert host == "example.test"
            assert port == 443
            assert type == socket.SOCK_STREAM

            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (
                        "93.184.216.34",
                        port,
                    ),
                )
            ]

        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            fake_getaddrinfo,
        )

        def handle_request(
            request: httpx.Request,
        ) -> httpx.Response:
            received_requests.append(
                request
            )

            return httpx.Response(
                204,
                request=request,
            )

        mock_transport = httpx.MockTransport(
            handle_request
        )

        monkeypatch.setattr(
            webhook_transport,
            "_build_pinned_http_transport",
            MagicMock(
                return_value=mock_transport,
            ),
        )

        worker = _worker_without_http_init()

        monkeypatch.setattr(
            settings,
            "WEBHOOK_SECRET_ENCRYPTION_KEY",
            encryption_key,
        )

        processed = worker.run_once()

        assert processed == 1
        assert len(received_requests) == 1

        request = received_requests[0]

        assert request.method == "POST"
        assert str(request.url) == (
            "https://example.test/webhook"
        )

        assert (
            request.headers[
                HEADER_DELIVERY_ID
            ]
            == str(delivery_id)
        )

        assert (
            request.headers[
                HEADER_DELIVERY_KEY
            ]
            == delivery_key
        )

        timestamp = request.headers[
            HEADER_TIMESTAMP
        ]

        assert timestamp.isdigit()

        expected_signature = (
            "sha256="
            + hmac.new(
                signing_secret.encode("utf-8"),
                (
                    timestamp.encode("ascii")
                    + b"."
                    + str(delivery_id).encode("utf-8")
                    + b"."
                    + delivery_key.encode("utf-8")
                    + b"."
                    + request.content
                ),
                hashlib.sha256,
            ).hexdigest()
        )

        assert (
            request.headers[
                HEADER_SIGNATURE
            ]
            == expected_signature
        )

        assert json.loads(
            request.content.decode("utf-8")
        ) == expected_payload

        verify_db = SessionLocal()

        persisted = (
            verify_db.query(
                AlertDelivery
            )
            .filter(
                AlertDelivery.id
                == delivery_id
            )
            .one()
        )

        assert (
            persisted.status
            == ALERT_DELIVERY_STATUS_DELIVERED
        )
        assert persisted.attempt_count == 1
        assert persisted.claimed_at is None
        assert persisted.next_attempt_at is None
        assert (
            persisted.last_attempt_at
            is not None
        )
        assert persisted.delivered_at is not None
        assert persisted.last_error is None

    finally:
        if verify_db is not None:
            verify_db.close()

        if setup_db.is_active:
            setup_db.close()

        _cleanup(organization_ids)

def test_worker_schedules_transient_webhook_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encryption_key = _encryption_key()
    setup_db = SessionLocal()
    verify_db = None
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            setup_db,
            prefix="retryable",
        )
        organization_ids.append(organization.id)

        alert = _make_alert(
            setup_db,
            organization_id=organization.id,
        )

        webhook = _make_webhook(
            setup_db,
            organization_id=organization.id,
            encryption_key=encryption_key,
        )

        delivery = _make_delivery(
            setup_db,
            organization_id=organization.id,
            alert_id=alert.id,
            webhook_id=webhook.id,
        )

        delivery_id = delivery.id

        setup_db.commit()
        setup_db.close()

        transport = MagicMock(
            side_effect=(
                TransientWebhookDeliveryError(
                    "temporary endpoint failure"
                )
            )
        )

        monkeypatch.setattr(
            worker_module,
            "deliver_webhook",
            transport,
        )

        worker = _worker_without_http_init()

        monkeypatch.setattr(
            settings,
            "WEBHOOK_SECRET_ENCRYPTION_KEY",
            encryption_key,
        )

        processed = worker.run_once()

        assert processed == 1
        transport.assert_called_once()

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
            == ALERT_DELIVERY_STATUS_RETRYABLE
        )
        assert persisted.attempt_count == 1
        assert persisted.claimed_at is None
        assert persisted.last_attempt_at is not None
        assert persisted.next_attempt_at is not None
        assert persisted.delivered_at is None
        assert (
            persisted.last_error
            == "temporary endpoint failure"
        )

    finally:
        if verify_db is not None:
            verify_db.close()

        if setup_db.is_active:
            setup_db.close()

        _cleanup(organization_ids)


def test_worker_terminalizes_destination_disabled_after_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encryption_key = _encryption_key()
    setup_db = SessionLocal()
    verify_db = None
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            setup_db,
            prefix="disabled",
        )
        organization_ids.append(organization.id)

        alert = _make_alert(
            setup_db,
            organization_id=organization.id,
        )

        webhook = _make_webhook(
            setup_db,
            organization_id=organization.id,
            encryption_key=encryption_key,
        )

        delivery = _make_delivery(
            setup_db,
            organization_id=organization.id,
            alert_id=alert.id,
            webhook_id=webhook.id,
        )

        delivery_id = delivery.id

        setup_db.commit()

        webhook.is_active = False
        setup_db.commit()
        setup_db.close()

        transport = MagicMock()

        monkeypatch.setattr(
            worker_module,
            "deliver_webhook",
            transport,
        )

        worker = _worker_without_http_init()

        monkeypatch.setattr(
            settings,
            "WEBHOOK_SECRET_ENCRYPTION_KEY",
            encryption_key,
        )

        processed = worker.run_once()

        assert processed == 1
        transport.assert_not_called()

        verify_db = SessionLocal()

        persisted = (
            verify_db.query(AlertDelivery)
            .filter(
                AlertDelivery.id == delivery_id
            )
            .one()
        )

        assert persisted.status == "failed"
        assert persisted.attempt_count == 1
        assert persisted.claimed_at is None
        assert persisted.delivered_at is None
        assert (
            persisted.last_error
            == "Webhook destination is not active."
        )

    finally:
        if verify_db is not None:
            verify_db.close()

        if setup_db.is_active:
            setup_db.close()

        _cleanup(organization_ids)