from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

import src.main as main_module

from src.api.routes import alerts as alerts_routes
from src.core.database import SessionLocal
from src.core.security import (
    CSRF_COOKIE,
    get_current_user,
)
from src.main import app
from src.models.alert import Alert
from src.models.alert_delivery import (
    ALERT_DELIVERY_DESTINATION_WEBHOOK,
    ALERT_DELIVERY_STATUS_DELIVERED,
    ALERT_DELIVERY_STATUS_FAILED,
    ALERT_DELIVERY_STATUS_PENDING,
    ALERT_DELIVERY_STATUS_RETRYABLE,
    AlertDelivery,
)
from src.models.organization import Organization
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


AUTH_HEADERS = {
    "Authorization": (
        "Bearer alert-delivery-test-token"
    ),
}


def _fake_user(
    organization_id: uuid.UUID,
    *,
    role: str = "user",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        email="alert-delivery-api@example.com",
        role=role,
    )


def _database_override():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


def _allow_auth_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "verify_token",
        lambda token: {
            "sub": str(
                uuid.uuid4()
            ),
        },
    )


def _override_authenticated_user(
    organization_id: uuid.UUID,
    *,
    role: str = "user",
) -> None:
    app.dependency_overrides[
        get_current_user
    ] = lambda: _fake_user(
        organization_id,
        role=role,
    )

    app.dependency_overrides[
        alerts_routes.get_db
    ] = _database_override


def _clear_overrides() -> None:
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    app.dependency_overrides.pop(
        alerts_routes.get_db,
        None,
    )


def _csrf_headers(
    client: TestClient,
) -> dict[str, str]:
    token = (
        "alert-delivery-csrf-token"
    )

    client.cookies.set(
        CSRF_COOKIE,
        token,
    )

    return {
        **AUTH_HEADERS,
        "X-CSRF-Token": token,
    }


def _create_organization(
    db,
    *,
    prefix: str,
) -> Organization:
    token = uuid.uuid4().hex

    organization = Organization(
        id=uuid.uuid4(),
        name=(
            f"Alert Delivery API {prefix} "
            f"{token[:8]}"
        ),
        slug=(
            f"alert-delivery-api-"
            f"{prefix.lower()}-"
            f"{token}"
        ),
        billing_email=(
            f"alert-delivery-api-"
            f"{token[:16]}"
            "@example.com"
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
                f"alert:{organization_id}:"
                f"{suffix}:{uuid.uuid4()}"
            ).encode(
                "utf-8"
            )
        ).hexdigest(),
        alert_type="pattern",
        title=(
            f"Alert delivery API {suffix}"
        ),
        message=(
            "Integration test alert"
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
    attempt_count: int = 0,
    next_attempt_at: datetime | None = None,
    claimed_at: datetime | None = None,
    last_attempt_at: datetime | None = None,
    delivered_at: datetime | None = None,
    last_error: str | None = None,
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
        last_attempt_at=last_attempt_at,
        delivered_at=delivered_at,
        last_error=last_error,
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
        db.rollback()

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


def test_list_alert_deliveries_is_tenant_scoped_and_serializes_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_auth_middleware(
        monkeypatch
    )

    setup_db = SessionLocal()

    organization_ids: list[
        uuid.UUID
    ] = []

    try:
        own_organization = (
            _create_organization(
                setup_db,
                prefix="ListOwn",
            )
        )

        foreign_organization = (
            _create_organization(
                setup_db,
                prefix="ListForeign",
            )
        )

        organization_ids.extend(
            [
                own_organization.id,
                foreign_organization.id,
            ]
        )

        own_alert = _create_alert(
            setup_db,
            organization_id=(
                own_organization.id
            ),
            suffix="own",
        )

        foreign_alert = _create_alert(
            setup_db,
            organization_id=(
                foreign_organization.id
            ),
            suffix="foreign",
        )

        lifecycle_time = datetime(
            2026,
            9,
            13,
            4,
            30,
            tzinfo=timezone.utc,
        )

        own_delivery = _create_delivery(
            setup_db,
            organization_id=(
                own_organization.id
            ),
            alert_id=own_alert.id,
            status=(
                ALERT_DELIVERY_STATUS_RETRYABLE
            ),
            attempt_count=2,
            next_attempt_at=lifecycle_time,
            last_attempt_at=lifecycle_time,
            last_error="upstream timeout",
        )

        _create_delivery(
            setup_db,
            organization_id=(
                foreign_organization.id
            ),
            alert_id=foreign_alert.id,
            status=(
                ALERT_DELIVERY_STATUS_RETRYABLE
            ),
            attempt_count=1,
        )

        own_organization_id = (
            own_organization.id
        )

        own_delivery_id = (
            own_delivery.id
        )

        own_alert_id = (
            own_alert.id
        )

        own_destination_id = (
            own_delivery.destination_id
        )

        setup_db.commit()

        _override_authenticated_user(
            own_organization_id
        )

        with TestClient(
            app
        ) as client:
            response = client.get(
                "/api/alerts/deliveries",
                headers=AUTH_HEADERS,
                params={
                    "status": "RETRYABLE",
                    "limit": 25,
                    "offset": 0,
                },
            )

            assert (
                response.status_code
                == 200
            ), response.text

            body = response.json()

            assert body["total_count"] == 1
            assert body["limit"] == 25
            assert body["offset"] == 0

            rows = body["data"]

            assert len(
                rows
            ) == 1

            row = rows[0]

            assert row["id"] == str(
                own_delivery_id
            )

            assert row["alert_id"] == str(
                own_alert_id
            )

            assert (
                row["destination_type"]
                == (
                    ALERT_DELIVERY_DESTINATION_WEBHOOK
                )
            )

            assert (
                row["destination_id"]
                == str(
                    own_destination_id
                )
            )

            assert (
                row["status"]
                == (
                    ALERT_DELIVERY_STATUS_RETRYABLE
                )
            )

            assert (
                row["attempt_count"]
                == 2
            )

            assert (
                row["last_error"]
                == "upstream timeout"
            )

            assert (
                row["next_attempt_at"]
                is not None
            )

            assert (
                row["last_attempt_at"]
                is not None
            )

            assert (
                row["claimed_at"]
                is None
            )

            assert (
                row["delivered_at"]
                is None
            )

            assert (
                row["created_at"]
                is not None
            )

    finally:
        _clear_overrides()

        setup_db.close()

        _cleanup(
            organization_ids
        )


def test_list_alert_deliveries_reports_filtered_total_beyond_current_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_auth_middleware(
        monkeypatch
    )

    setup_db = SessionLocal()
    organization_ids: list[
        uuid.UUID
    ] = []

    try:
        organization = _create_organization(
            setup_db,
            prefix="Pagination",
        )

        organization_ids.append(
            organization.id
        )

        alert = _create_alert(
            setup_db,
            organization_id=organization.id,
            suffix="pagination",
        )

        for _ in range(3):
            _create_delivery(
                setup_db,
                organization_id=organization.id,
                alert_id=alert.id,
                status=(
                    ALERT_DELIVERY_STATUS_RETRYABLE
                ),
                attempt_count=1,
            )

        _create_delivery(
            setup_db,
            organization_id=organization.id,
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_FAILED
            ),
            attempt_count=1,
        )

        setup_db.commit()

        _override_authenticated_user(
            organization.id
        )

        with TestClient(
            app
        ) as client:
            response = client.get(
                "/api/alerts/deliveries",
                headers=AUTH_HEADERS,
                params={
                    "status": "retryable",
                    "limit": 1,
                    "offset": 1,
                },
            )

        assert response.status_code == 200

        body = response.json()

        assert len(body["data"]) == 1
        assert body["total_count"] == 3
        assert body["limit"] == 1
        assert body["offset"] == 1

    finally:
        _clear_overrides()
        setup_db.close()
        _cleanup(
            organization_ids
        )


def test_get_alert_delivery_is_tenant_scoped_and_rejects_malformed_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_auth_middleware(
        monkeypatch
    )

    setup_db = SessionLocal()

    organization_ids: list[
        uuid.UUID
    ] = []

    try:
        own_organization = (
            _create_organization(
                setup_db,
                prefix="GetOwn",
            )
        )

        foreign_organization = (
            _create_organization(
                setup_db,
                prefix="GetForeign",
            )
        )

        organization_ids.extend(
            [
                own_organization.id,
                foreign_organization.id,
            ]
        )

        own_alert = _create_alert(
            setup_db,
            organization_id=(
                own_organization.id
            ),
            suffix="get-own",
        )

        foreign_alert = _create_alert(
            setup_db,
            organization_id=(
                foreign_organization.id
            ),
            suffix="get-foreign",
        )

        own_delivery = _create_delivery(
            setup_db,
            organization_id=(
                own_organization.id
            ),
            alert_id=own_alert.id,
            status=(
                ALERT_DELIVERY_STATUS_PENDING
            ),
        )

        foreign_delivery = (
            _create_delivery(
                setup_db,
                organization_id=(
                    foreign_organization.id
                ),
                alert_id=(
                    foreign_alert.id
                ),
                status=(
                    ALERT_DELIVERY_STATUS_FAILED
                ),
                attempt_count=2,
            )
        )

        own_organization_id = (
            own_organization.id
        )

        own_delivery_id = (
            own_delivery.id
        )

        foreign_delivery_id = (
            foreign_delivery.id
        )

        setup_db.commit()

        _override_authenticated_user(
            own_organization_id
        )

        with TestClient(
            app
        ) as client:
            response = client.get(
                (
                    "/api/alerts/deliveries/"
                    f"{own_delivery_id}"
                ),
                headers=AUTH_HEADERS,
            )

            assert (
                response.status_code
                == 200
            ), response.text

            assert (
                response.json()[
                    "data"
                ][
                    "id"
                ]
                == str(
                    own_delivery_id
                )
            )

            response = client.get(
                (
                    "/api/alerts/deliveries/"
                    f"{foreign_delivery_id}"
                ),
                headers=AUTH_HEADERS,
            )

            assert (
                response.status_code
                == 404
            ), response.text

            response = client.get(
                (
                    "/api/alerts/deliveries/"
                    "not-a-uuid"
                ),
                headers=AUTH_HEADERS,
            )

            assert (
                response.status_code
                == 404
            ), response.text

            assert (
                response.json()[
                    "detail"
                ]
                == "Alert delivery not found"
            )

    finally:
        _clear_overrides()

        setup_db.close()

        _cleanup(
            organization_ids
        )


def test_list_alert_deliveries_rejects_invalid_filters_and_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_auth_middleware(
        monkeypatch
    )

    setup_db = SessionLocal()

    organization_ids: list[
        uuid.UUID
    ] = []

    try:
        organization = (
            _create_organization(
                setup_db,
                prefix="Validation",
            )
        )

        organization_ids.append(
            organization.id
        )

        organization_id = (
            organization.id
        )

        setup_db.commit()

        _override_authenticated_user(
            organization_id
        )

        with TestClient(
            app
        ) as client:
            for params in (
                {
                    "status": "unknown",
                },
                {
                    "limit": 0,
                },
                {
                    "limit": 501,
                },
                {
                    "offset": -1,
                },
            ):
                response = client.get(
                    "/api/alerts/deliveries",
                    headers=AUTH_HEADERS,
                    params=params,
                )

                assert (
                    response.status_code
                    == 422
                ), (
                    params,
                    response.text,
                )

    finally:
        _clear_overrides()

        setup_db.close()

        _cleanup(
            organization_ids
        )


def test_retry_alert_delivery_persists_transition_without_resetting_attempt_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_auth_middleware(
        monkeypatch
    )

    setup_db = SessionLocal()

    organization_ids: list[
        uuid.UUID
    ] = []

    try:
        organization = (
            _create_organization(
                setup_db,
                prefix="Retry",
            )
        )

        organization_ids.append(
            organization.id
        )

        alert = _create_alert(
            setup_db,
            organization_id=(
                organization.id
            ),
            suffix="retry",
        )

        claimed_at = datetime(
            2026,
            9,
            13,
            4,
            40,
            tzinfo=timezone.utc,
        )

        last_attempt_at = datetime(
            2026,
            9,
            13,
            4,
            39,
            tzinfo=timezone.utc,
        )

        delivery = _create_delivery(
            setup_db,
            organization_id=(
                organization.id
            ),
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_FAILED
            ),
            attempt_count=2,
            claimed_at=claimed_at,
            last_attempt_at=(
                last_attempt_at
            ),
            last_error="HTTP 503",
        )

        organization_id = (
            organization.id
        )

        delivery_id = (
            delivery.id
        )

        setup_db.commit()

        _override_authenticated_user(
            organization_id
        )

        with TestClient(
            app
        ) as client:
            response = client.post(
                (
                    "/api/alerts/deliveries/"
                    f"{delivery_id}"
                    "/retry"
                ),
                headers=_csrf_headers(
                    client
                ),
            )

            assert (
                response.status_code
                == 200
            ), response.text

            retried = response.json()[
                "data"
            ]

            assert (
                retried["status"]
                == (
                    ALERT_DELIVERY_STATUS_RETRYABLE
                )
            )

            assert (
                retried["attempt_count"]
                == 2
            )

            assert (
                retried["retry_allowed"]
                is True
            )

            assert (
                retried["retry_block_reason"]
                is None
            )

            assert (
                retried["claimed_at"]
                is None
            )

            assert (
                retried["next_attempt_at"]
                is not None
            )

            assert (
                retried["last_attempt_at"]
                is not None
            )

            assert (
                retried["last_error"]
                == "HTTP 503"
            )

        verify_db = SessionLocal()

        try:
            stored = (
                verify_db.query(
                    AlertDelivery
                )
                .filter(
                    AlertDelivery.id
                    == delivery_id,
                    AlertDelivery.organization_id
                    == organization_id,
                )
                .one()
            )

            assert (
                stored.status
                == (
                    ALERT_DELIVERY_STATUS_RETRYABLE
                )
            )

            assert (
                stored.attempt_count
                == 2
            )

            assert (
                stored.claimed_at
                is None
            )

            assert (
                stored.next_attempt_at
                is not None
            )

            assert (
                stored.last_attempt_at
                == last_attempt_at
            )

            assert (
                stored.last_error
                == "HTTP 503"
            )

        finally:
            verify_db.close()

    finally:
        _clear_overrides()

        setup_db.close()

        _cleanup(
            organization_ids
        )


def test_retry_alert_delivery_rejects_viewer_without_mutating_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_auth_middleware(
        monkeypatch
    )

    setup_db = SessionLocal()

    organization_ids: list[
        uuid.UUID
    ] = []

    try:
        organization = (
            _create_organization(
                setup_db,
                prefix="RetryViewer",
            )
        )

        organization_ids.append(
            organization.id
        )

        alert = _create_alert(
            setup_db,
            organization_id=(
                organization.id
            ),
            suffix="retry-viewer",
        )

        claimed_at = datetime(
            2026,
            9,
            13,
            4,
            40,
            tzinfo=timezone.utc,
        )

        last_attempt_at = datetime(
            2026,
            9,
            13,
            4,
            39,
            tzinfo=timezone.utc,
        )

        delivery = _create_delivery(
            setup_db,
            organization_id=(
                organization.id
            ),
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_FAILED
            ),
            attempt_count=2,
            claimed_at=claimed_at,
            last_attempt_at=(
                last_attempt_at
            ),
            last_error="HTTP 503",
        )

        organization_id = (
            organization.id
        )

        delivery_id = (
            delivery.id
        )

        setup_db.commit()

        _override_authenticated_user(
            organization_id,
            role="viewer",
        )

        with TestClient(
            app
        ) as client:
            response = client.post(
                (
                    "/api/alerts/deliveries/"
                    f"{delivery_id}"
                    "/retry"
                ),
                headers=_csrf_headers(
                    client
                ),
            )

        assert (
            response.status_code
            == 403
        ), response.text

        detail = response.json()[
            "detail"
        ]

        assert (
            detail["error"]["code"]
            == "AUTH_013"
        )

        verify_db = SessionLocal()

        try:
            stored = (
                verify_db.query(
                    AlertDelivery
                )
                .filter(
                    AlertDelivery.id
                    == delivery_id,
                    AlertDelivery.organization_id
                    == organization_id,
                )
                .one()
            )

            assert (
                stored.status
                == ALERT_DELIVERY_STATUS_FAILED
            )

            assert (
                stored.attempt_count
                == 2
            )

            assert (
                stored.claimed_at
                == claimed_at
            )

            assert (
                stored.next_attempt_at
                is None
            )

            assert (
                stored.last_attempt_at
                == last_attempt_at
            )

            assert (
                stored.last_error
                == "HTTP 503"
            )

        finally:
            verify_db.close()

    finally:
        _clear_overrides()

        setup_db.close()

        _cleanup(
            organization_ids
        )


def test_retry_alert_delivery_is_tenant_isolated_and_rejects_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_auth_middleware(
        monkeypatch
    )

    setup_db = SessionLocal()

    organization_ids: list[
        uuid.UUID
    ] = []

    try:
        own_organization = (
            _create_organization(
                setup_db,
                prefix="RetryOwn",
            )
        )

        foreign_organization = (
            _create_organization(
                setup_db,
                prefix="RetryForeign",
            )
        )

        organization_ids.extend(
            [
                own_organization.id,
                foreign_organization.id,
            ]
        )

        own_alert = _create_alert(
            setup_db,
            organization_id=(
                own_organization.id
            ),
            suffix="terminal",
        )

        foreign_alert = _create_alert(
            setup_db,
            organization_id=(
                foreign_organization.id
            ),
            suffix="foreign-retry",
        )

        delivered = _create_delivery(
            setup_db,
            organization_id=(
                own_organization.id
            ),
            alert_id=own_alert.id,
            status=(
                ALERT_DELIVERY_STATUS_DELIVERED
            ),
            attempt_count=1,
            delivered_at=datetime(
                2026,
                9,
                13,
                4,
                45,
                tzinfo=timezone.utc,
            ),
        )

        foreign_delivery = (
            _create_delivery(
                setup_db,
                organization_id=(
                    foreign_organization.id
                ),
                alert_id=(
                    foreign_alert.id
                ),
                status=(
                    ALERT_DELIVERY_STATUS_FAILED
                ),
                attempt_count=1,
            )
        )

        own_organization_id = (
            own_organization.id
        )

        delivered_id = (
            delivered.id
        )

        foreign_delivery_id = (
            foreign_delivery.id
        )

        setup_db.commit()

        _override_authenticated_user(
            own_organization_id
        )

        with TestClient(
            app
        ) as client:
            headers = _csrf_headers(
                client
            )

            detail_response = client.get(
                (
                    "/api/alerts/deliveries/"
                    f"{delivered_id}"
                ),
                headers=AUTH_HEADERS,
            )

            assert (
                detail_response.status_code
                == 200
            ), detail_response.text

            delivered_detail = (
                detail_response.json()["data"]
            )

            assert (
                delivered_detail["retry_allowed"]
                is False
            )

            assert (
                delivered_detail[
                    "retry_block_reason"
                ]
                == "already_delivered"
            )

            response = client.post(
                (
                    "/api/alerts/deliveries/"
                    f"{delivered_id}"
                    "/retry"
                ),
                headers=headers,
            )

            assert (
                response.status_code
                == 409
            ), response.text

            response = client.post(
                (
                    "/api/alerts/deliveries/"
                    f"{foreign_delivery_id}"
                    "/retry"
                ),
                headers=headers,
            )

            assert (
                response.status_code
                == 404
            ), response.text

        verify_db = SessionLocal()

        try:
            own_stored = (
                verify_db.query(
                    AlertDelivery
                )
                .filter(
                    AlertDelivery.id
                    == delivered_id
                )
                .one()
            )

            foreign_stored = (
                verify_db.query(
                    AlertDelivery
                )
                .filter(
                    AlertDelivery.id
                    == foreign_delivery_id
                )
                .one()
            )

            assert (
                own_stored.status
                == (
                    ALERT_DELIVERY_STATUS_DELIVERED
                )
            )

            assert (
                foreign_stored.status
                == (
                    ALERT_DELIVERY_STATUS_FAILED
                )
            )

        finally:
            verify_db.close()

    finally:
        _clear_overrides()

        setup_db.close()

        _cleanup(
            organization_ids
        )

def test_retry_alert_delivery_rejects_malformed_id_and_exhausted_attempt_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_auth_middleware(
        monkeypatch
    )

    setup_db = SessionLocal()

    organization_ids: list[
        uuid.UUID
    ] = []

    try:
        organization = (
            _create_organization(
                setup_db,
                prefix="RetryValidation",
            )
        )

        organization_ids.append(
            organization.id
        )

        alert = _create_alert(
            setup_db,
            organization_id=(
                organization.id
            ),
            suffix="retry-validation",
        )

        delivery = _create_delivery(
            setup_db,
            organization_id=(
                organization.id
            ),
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_FAILED
            ),
            attempt_count=(
                DEFAULT_MAX_ATTEMPTS
            ),
            last_error="retry budget exhausted",
        )

        organization_id = (
            organization.id
        )
        delivery_id = (
            delivery.id
        )

        setup_db.commit()

        _override_authenticated_user(
            organization_id
        )

        with TestClient(
            app
        ) as client:
            headers = _csrf_headers(
                client
            )

            response = client.post(
                (
                    "/api/alerts/deliveries/"
                    "not-a-uuid/retry"
                ),
                headers=headers,
            )

            assert (
                response.status_code
                == 404
            ), response.text

            assert (
                response.json()[
                    "detail"
                ]
                == "Alert delivery not found"
            )

            detail_response = client.get(
                (
                    "/api/alerts/deliveries/"
                    f"{delivery_id}"
                ),
                headers=AUTH_HEADERS,
            )

            assert (
                detail_response.status_code
                == 200
            ), detail_response.text

            exhausted_detail = (
                detail_response.json()["data"]
            )

            assert (
                exhausted_detail["retry_allowed"]
                is False
            )

            assert (
                exhausted_detail[
                    "retry_block_reason"
                ]
                == "attempt_budget_exhausted"
            )

            response = client.post(
                (
                    "/api/alerts/deliveries/"
                    f"{delivery_id}"
                    "/retry"
                ),
                headers=headers,
            )

            assert (
                response.status_code
                == 409
            ), response.text

            assert (
                response.json()[
                    "detail"
                ]
                == (
                    "Alert delivery attempt "
                    "budget is exhausted."
                )
            )

        verify_db = SessionLocal()

        try:
            stored = (
                verify_db.query(
                    AlertDelivery
                )
                .filter(
                    AlertDelivery.id
                    == delivery_id,
                    AlertDelivery.organization_id
                    == organization_id,
                )
                .one()
            )

            assert (
                stored.status
                == ALERT_DELIVERY_STATUS_FAILED
            )
            assert (
                stored.attempt_count
                == DEFAULT_MAX_ATTEMPTS
            )
            assert (
                stored.next_attempt_at
                is None
            )
            assert (
                stored.last_error
                == "retry budget exhausted"
            )

        finally:
            verify_db.close()

    finally:
        _clear_overrides()

        setup_db.close()

        _cleanup(
            organization_ids
        )

def test_retry_alert_delivery_requires_csrf_and_does_not_mutate_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_auth_middleware(
        monkeypatch
    )

    setup_db = SessionLocal()

    organization_ids: list[
        uuid.UUID
    ] = []

    try:
        organization = (
            _create_organization(
                setup_db,
                prefix="Csrf",
            )
        )

        organization_ids.append(
            organization.id
        )

        alert = _create_alert(
            setup_db,
            organization_id=(
                organization.id
            ),
            suffix="csrf",
        )

        delivery = _create_delivery(
            setup_db,
            organization_id=(
                organization.id
            ),
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_FAILED
            ),
            attempt_count=2,
            last_error="timeout",
        )

        organization_id = (
            organization.id
        )

        delivery_id = (
            delivery.id
        )

        setup_db.commit()

        _override_authenticated_user(
            organization_id
        )

        with TestClient(
            app
        ) as client:
            response = client.post(
                (
                    "/api/alerts/deliveries/"
                    f"{delivery_id}"
                    "/retry"
                ),
                headers=AUTH_HEADERS,
            )

            assert (
                response.status_code
                == 403
            ), response.text

        verify_db = SessionLocal()

        try:
            stored = (
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
                stored.status
                == (
                    ALERT_DELIVERY_STATUS_FAILED
                )
            )

            assert (
                stored.attempt_count
                == 2
            )

            assert (
                stored.next_attempt_at
                is None
            )

            assert (
                stored.last_error
                == "timeout"
            )

        finally:
            verify_db.close()

    finally:
        _clear_overrides()

        setup_db.close()

        _cleanup(
            organization_ids
        )

def test_delivery_summary_is_tenant_scoped_and_serializes_queue_health(
    monkeypatch: pytest.MonkeyPatch,
):
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    now = datetime.now(
        timezone.utc
    )

    try:
        organization = _create_organization(
            db,
            prefix="Summary",
        )
        foreign_organization = (
            _create_organization(
                db,
                prefix="SummaryForeign",
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
            suffix="summary",
        )
        foreign_alert = _create_alert(
            db,
            organization_id=(
                foreign_organization.id
            ),
            suffix="summary-foreign",
        )

        _create_delivery(
            db,
            organization_id=(
                organization.id
            ),
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_PENDING
            ),
            attempt_count=0,
        )

        _create_delivery(
            db,
            organization_id=(
                organization.id
            ),
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_RETRYABLE
            ),
            attempt_count=2,
            next_attempt_at=(
                now
                - timedelta(
                    seconds=1
                )
            ),
        )

        stale_delivery = _create_delivery(
            db,
            organization_id=(
                organization.id
            ),
            alert_id=alert.id,
            status="processing",
            attempt_count=3,
            claimed_at=(
                now
                - timedelta(
                    seconds=(
                        DEFAULT_CLAIM_TIMEOUT_SECONDS
                        + 30
                    )
                )
            ),
        )

        _create_delivery(
            db,
            organization_id=(
                organization.id
            ),
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_FAILED
            ),
            attempt_count=(
                DEFAULT_MAX_ATTEMPTS
            ),
            last_error=(
                "Maximum delivery attempts "
                "exhausted."
            ),
        )

        _create_delivery(
            db,
            organization_id=(
                organization.id
            ),
            alert_id=alert.id,
            status=(
                ALERT_DELIVERY_STATUS_DELIVERED
            ),
            attempt_count=1,
            delivered_at=now,
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
        )

        db.commit()

        _allow_auth_middleware(
            monkeypatch
        )
        _override_authenticated_user(
            organization.id
        )

        with TestClient(
            app
        ) as client:
            response = client.get(
                "/api/alerts/deliveries/summary",
                headers=AUTH_HEADERS,
            )

        assert response.status_code == 200

        body = response.json()
        data = body["data"]

        assert (
            data["organization_id"]
            == str(
                organization.id
            )
        )

        assert data["observed_at"]

        assert data["total_count"] == 5

        assert data["pending_count"] == 1
        assert data["processing_count"] == 1
        assert data["retryable_count"] == 1
        assert data["failed_count"] == 1
        assert data["delivered_count"] == 1

        assert data["claimable_count"] == 3
        assert (
            data["stale_processing_count"]
            == 1
        )
        assert data["exhausted_count"] == 0

        assert (
            data["total_attempt_count"]
            == 11
        )

        assert data["oldest_pending_at"]
        assert data["oldest_retryable_at"]

        assert (
            data[
                "oldest_processing_claimed_at"
            ]
            == stale_delivery.claimed_at.isoformat()
        )

        assert set(
            data
        ) == {
            "organization_id",
            "observed_at",
            "total_count",
            "pending_count",
            "processing_count",
            "retryable_count",
            "failed_count",
            "delivered_count",
            "claimable_count",
            "stale_processing_count",
            "exhausted_count",
            "total_attempt_count",
            "oldest_pending_at",
            "oldest_retryable_at",
            "oldest_processing_claimed_at",
        }

    finally:
        _clear_overrides()
        db.close()

        _cleanup(
            organization_ids
        )