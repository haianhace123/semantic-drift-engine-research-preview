from __future__ import annotations

import base64
import os
import socket
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

import src.main as main_module

from src.api.routes import alerts as alerts_routes
from src.core.config import settings
from src.core.database import SessionLocal
from src.core.security import (
    CSRF_COOKIE,
    get_current_user,
)
from src.main import app
from src.models.organization import Organization
from src.models.webhook_subscription import (
    WebhookSubscription,
)
from src.services.webhook_secret_encryption_service import (
    EncryptedWebhookSecret,
    decrypt_webhook_secret,
    encrypt_webhook_secret,
)

import src.services.webhook_delivery_transport_service as webhook_transport


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
        "Bearer webhook-subscription-test-token"
    ),
}


WEBHOOK_SECRET_ENCRYPTION_KEY = (
    base64.urlsafe_b64encode(
        b"\x42" * 32
    ).decode("ascii")
)


def _configure_webhook_secret_encryption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "WEBHOOK_SECRET_ENCRYPTION_KEY",
        WEBHOOK_SECRET_ENCRYPTION_KEY,
    )


def _fake_user(
    organization_id: uuid.UUID,
    *,
    role: str = "admin",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        email="webhook-subscription-api@example.com",
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
    role: str = "admin",
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
        "webhook-subscription-csrf-token"
    )

    client.cookies.set(
        CSRF_COOKIE,
        token,
    )

    return {
        **AUTH_HEADERS,
        "X-CSRF-Token": token,
    }


def _mock_public_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_getaddrinfo = (
        socket.getaddrinfo
    )

    def fake_getaddrinfo(
        host,
        port,
        *args,
        **kwargs,
    ):
        if (
            str(host)
            == "hooks.example.test"
        ):
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (
                        "93.184.216.34",
                        port,
                    ),
                ),
            ]

        return original_getaddrinfo(
            host,
            port,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        webhook_transport.socket,
        "getaddrinfo",
        fake_getaddrinfo,
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
            f"Webhook API {prefix} "
            f"{token[:8]}"
        ),
        slug=(
            f"webhook-api-"
            f"{prefix.lower()}-"
            f"{token}"
        ),
        billing_email=(
            f"webhook-api-"
            f"{token[:16]}"
            "@example.com"
        ),
    )

    db.add(
        organization
    )

    db.flush()

    return organization


def _create_subscription(
    db,
    *,
    organization_id: uuid.UUID,
    url: str = (
        "https://hooks.example.test/sde"
    ),
    events: list[str] | None = None,
    secret_key: str = "existing-secret",
    is_active: bool = True,
) -> WebhookSubscription:
    subscription_id = uuid.uuid4()

    encrypted = encrypt_webhook_secret(
        secret_key,
        organization_id=organization_id,
        subscription_id=subscription_id,
        encoded_encryption_key=(
            WEBHOOK_SECRET_ENCRYPTION_KEY
        ),
    )

    subscription = WebhookSubscription(
        id=subscription_id,
        organization_id=organization_id,
        url=url,
        events=(
            events
            if events is not None
            else ["high_risk"]
        ),
        secret_ciphertext=(
            encrypted.ciphertext
        ),
        secret_nonce=encrypted.nonce,
        secret_encryption_version=(
            encrypted.version
        ),
        is_active=is_active,
    )

    db.add(
        subscription
    )

    db.flush()

    return subscription


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
                WebhookSubscription
            ).where(
                WebhookSubscription.organization_id.in_(
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


def test_create_and_list_webhook_subscription_discloses_secret_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_webhook_secret_encryption(
        monkeypatch
    )

    _allow_auth_middleware(
        monkeypatch
    )

    _mock_public_dns(
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
                prefix="Create",
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
            response = client.post(
                "/api/alerts/webhooks",
                headers=_csrf_headers(
                    client
                ),
                json={
                    "url": (
                        "https://hooks.example.test/sde"
                    ),
                    "events": [
                        "pattern",
                        "anomaly",
                    ],
                },
            )

            assert (
                response.status_code
                == 201
            ), response.text

            created = (
                response.json()[
                    "data"
                ]
            )

            subscription_id = (
                uuid.UUID(
                    created["id"]
                )
            )

            secret_key = (
                created[
                    "secret_key"
                ]
            )

            assert secret_key
            assert (
                created["url"]
                == (
                    "https://hooks.example.test/sde"
                )
            )

            assert (
                created["events"]
                == [
                    "pattern",
                    "anomaly",
                ]
            )

            assert (
                created["is_active"]
                is True
            )

            response = client.get(
                "/api/alerts/webhooks",
                headers=AUTH_HEADERS,
            )

            assert (
                response.status_code
                == 200
            ), response.text

            rows = (
                response.json()[
                    "data"
                ]
            )

            assert len(
                rows
            ) == 1

            listed = rows[0]

            assert (
                listed["id"]
                == str(
                    subscription_id
                )
            )

            assert (
                "secret_key"
                not in listed
            )

        verify_db = SessionLocal()

        try:
            subscription = (
                verify_db.query(
                    WebhookSubscription
                )
                .filter(
                    WebhookSubscription.id
                    == subscription_id,
                    WebhookSubscription.organization_id
                    == organization_id,
                )
                .one()
            )

            assert (
                subscription.secret_ciphertext
                is not None
            )

            assert (
                secret_key.encode("utf-8")
                not in subscription.secret_ciphertext
            )

            assert (
                subscription.secret_nonce
                is not None
            )

            assert (
                len(
                    subscription.secret_nonce
                )
                == 12
            )

            assert (
                subscription.secret_encryption_version
                == 1
            )

            encrypted = EncryptedWebhookSecret(
                version=(
                    subscription.secret_encryption_version
                ),
                nonce=subscription.secret_nonce,
                ciphertext=(
                    subscription.secret_ciphertext
                ),
            )

            assert (
                decrypt_webhook_secret(
                    encrypted,
                    organization_id=organization_id,
                    subscription_id=subscription_id,
                    encoded_encryption_key=(
                        WEBHOOK_SECRET_ENCRYPTION_KEY
                    ),
                )
                == secret_key
            )

            assert (
                subscription.events
                == [
                    "pattern",
                    "anomaly",
                ]
            )

            assert (
                subscription.is_active
                is True
            )
        finally:
            verify_db.close()

    finally:
        _clear_overrides()

        setup_db.close()

        _cleanup(
            organization_ids
        )


def test_rotate_secret_and_soft_disable_webhook_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_webhook_secret_encryption(
        monkeypatch
    )

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
                prefix="Lifecycle",
            )
        )

        organization_ids.append(
            organization.id
        )

        subscription = (
            _create_subscription(
                setup_db,
                organization_id=(
                    organization.id
                ),
                secret_key=(
                    "old-webhook-secret"
                ),
            )
        )

        organization_id = (
            organization.id
        )

        subscription_id = (
            subscription.id
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
                    "/api/alerts/webhooks/"
                    f"{subscription_id}"
                    "/rotate-secret"
                ),
                headers=headers,
            )

            assert (
                response.status_code
                == 200
            ), response.text

            rotated = (
                response.json()[
                    "data"
                ]
            )

            assert (
                rotated["id"]
                == str(
                    subscription_id
                )
            )

            new_secret = (
                rotated[
                    "secret_key"
                ]
            )

            assert new_secret
            assert (
                new_secret
                != "old-webhook-secret"
            )

            response = client.delete(
                (
                    "/api/alerts/webhooks/"
                    f"{subscription_id}"
                ),
                headers=headers,
            )

            assert (
                response.status_code
                == 200
            ), response.text

            deleted = (
                response.json()[
                    "data"
                ]
            )

            assert (
                deleted["id"]
                == str(
                    subscription_id
                )
            )

            assert (
                deleted[
                    "is_active"
                ]
                is False
            )

            assert (
                "secret_key"
                not in deleted
            )

            response = client.get(
                "/api/alerts/webhooks",
                headers=AUTH_HEADERS,
            )

            assert (
                response.status_code
                == 200
            ), response.text

            rows = (
                response.json()[
                    "data"
                ]
            )

            assert len(
                rows
            ) == 1

            assert (
                rows[0][
                    "is_active"
                ]
                is False
            )

            assert (
                "secret_key"
                not in rows[0]
            )

        verify_db = SessionLocal()

        try:
            stored = (
                verify_db.query(
                    WebhookSubscription
                )
                .filter(
                    WebhookSubscription.id
                    == subscription_id
                )
                .one()
            )

            assert (
                stored.secret_ciphertext
                is not None
            )

            assert (
                new_secret.encode("utf-8")
                not in stored.secret_ciphertext
            )

            assert (
                stored.secret_nonce
                is not None
            )

            assert (
                len(
                    stored.secret_nonce
                )
                == 12
            )

            assert (
                stored.secret_encryption_version
                == 1
            )

            encrypted = EncryptedWebhookSecret(
                version=(
                    stored.secret_encryption_version
                ),
                nonce=stored.secret_nonce,
                ciphertext=(
                    stored.secret_ciphertext
                ),
            )

            assert (
                decrypt_webhook_secret(
                    encrypted,
                    organization_id=organization_id,
                    subscription_id=subscription_id,
                    encoded_encryption_key=(
                        WEBHOOK_SECRET_ENCRYPTION_KEY
                    ),
                )
                == new_secret
            )

            assert (
                stored.is_active
                is False
            )
        finally:
            verify_db.close()

    finally:
        _clear_overrides()

        setup_db.close()

        _cleanup(
            organization_ids
        )


def test_webhook_subscription_mutations_require_admin_role_without_mutating_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_auth_middleware(
        monkeypatch
    )

    _mock_public_dns(
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
                prefix="Role",
            )
        )

        organization_ids.append(
            organization.id
        )

        subscription = (
            _create_subscription(
                setup_db,
                organization_id=(
                    organization.id
                ),
                secret_key=(
                    "role-original-secret"
                ),
            )
        )

        organization_id = (
            organization.id
        )

        subscription_id = (
            subscription.id
        )

        setup_db.commit()

        _override_authenticated_user(
            organization_id,
            role="user",
        )

        with TestClient(
            app
        ) as client:
            headers = _csrf_headers(
                client
            )

            create_response = client.post(
                "/api/alerts/webhooks",
                headers=headers,
                json={
                    "url": (
                        "https://hooks.example.test/role"
                    ),
                    "events": [
                        "pattern"
                    ],
                },
            )

            rotate_response = client.post(
                (
                    "/api/alerts/webhooks/"
                    f"{subscription_id}"
                    "/rotate-secret"
                ),
                headers=headers,
            )

            deactivate_response = client.delete(
                (
                    "/api/alerts/webhooks/"
                    f"{subscription_id}"
                ),
                headers=headers,
            )

        for response in (
            create_response,
            rotate_response,
            deactivate_response,
        ):
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
            rows = (
                verify_db.query(
                    WebhookSubscription
                )
                .filter(
                    WebhookSubscription.organization_id
                    == organization_id
                )
                .all()
            )

            assert len(
                rows
            ) == 1

            stored = rows[0]

            assert (
                stored.id
                == subscription_id
            )

            assert (
                decrypt_webhook_secret(
                    EncryptedWebhookSecret(
                        version=(
                            stored.secret_encryption_version
                        ),
                        nonce=stored.secret_nonce,
                        ciphertext=(
                            stored.secret_ciphertext
                        ),
                    ),
                    organization_id=organization_id,
                    subscription_id=subscription_id,
                    encoded_encryption_key=(
                        WEBHOOK_SECRET_ENCRYPTION_KEY
                    ),
                )
                == "role-original-secret"
            )

            assert (
                stored.is_active
                is True
            )

        finally:
            verify_db.close()

    finally:
        _clear_overrides()

        setup_db.close()

        _cleanup(
            organization_ids
        )


def test_webhook_subscription_mutations_are_tenant_isolated(
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
                prefix="Own",
            )
        )

        foreign_organization = (
            _create_organization(
                setup_db,
                prefix="Foreign",
            )
        )

        organization_ids.extend(
            [
                own_organization.id,
                foreign_organization.id,
            ]
        )

        foreign_subscription = (
            _create_subscription(
                setup_db,
                organization_id=(
                    foreign_organization.id
                ),
                secret_key=(
                    "foreign-secret"
                ),
            )
        )

        own_organization_id = (
            own_organization.id
        )

        foreign_subscription_id = (
            foreign_subscription.id
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

            response = client.get(
                "/api/alerts/webhooks",
                headers=AUTH_HEADERS,
            )

            assert (
                response.status_code
                == 200
            ), response.text

            assert (
                response.json()[
                    "data"
                ]
                == []
            )

            response = client.post(
                (
                    "/api/alerts/webhooks/"
                    f"{foreign_subscription_id}"
                    "/rotate-secret"
                ),
                headers=headers,
            )

            assert (
                response.status_code
                == 404
            ), response.text

            response = client.delete(
                (
                    "/api/alerts/webhooks/"
                    f"{foreign_subscription_id}"
                ),
                headers=headers,
            )

            assert (
                response.status_code
                == 404
            ), response.text

        verify_db = SessionLocal()

        try:
            stored = (
                verify_db.query(
                    WebhookSubscription
                )
                .filter(
                    WebhookSubscription.id
                    == foreign_subscription_id
                )
                .one()
            )

            assert (
                decrypt_webhook_secret(
                    EncryptedWebhookSecret(
                        version=(
                            stored.secret_encryption_version
                        ),
                        nonce=stored.secret_nonce,
                        ciphertext=(
                            stored.secret_ciphertext
                        ),
                    ),
                    organization_id=(
                        foreign_organization.id
                    ),
                    subscription_id=(
                        foreign_subscription_id
                    ),
                    encoded_encryption_key=(
                        WEBHOOK_SECRET_ENCRYPTION_KEY
                    ),
                )
                == "foreign-secret"
            )

            assert (
                stored.is_active
                is True
            )
        finally:
            verify_db.close()

    finally:
        _clear_overrides()

        setup_db.close()

        _cleanup(
            organization_ids
        )


def test_create_rejects_invalid_events_and_private_webhook_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_auth_middleware(
        monkeypatch
    )

    _mock_public_dns(
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
            headers = _csrf_headers(
                client
            )

            response = client.post(
                "/api/alerts/webhooks",
                headers=headers,
                json={
                    "url": (
                        "https://hooks.example.test/sde"
                    ),
                    "events": [
                        "*",
                        "pattern",
                    ],
                },
            )

            assert (
                response.status_code
                == 422
            ), response.text

            assert (
                "cannot be combined"
                in response.json()[
                    "detail"
                ]
            )

            response = client.post(
                "/api/alerts/webhooks",
                headers=headers,
                json={
                    "url": (
                        "https://127.0.0.1/webhook"
                    ),
                    "events": [
                        "pattern"
                    ],
                },
            )

            assert (
                response.status_code
                == 422
            ), response.text

        verify_db = SessionLocal()

        try:
            count = (
                verify_db.query(
                    WebhookSubscription
                )
                .filter(
                    WebhookSubscription.organization_id
                    == organization_id
                )
                .count()
            )

            assert count == 0
        finally:
            verify_db.close()

    finally:
        _clear_overrides()

        setup_db.close()

        _cleanup(
            organization_ids
        )


def test_webhook_subscription_mutations_require_csrf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_auth_middleware(
        monkeypatch
    )

    _mock_public_dns(
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

        subscription = (
            _create_subscription(
                setup_db,
                organization_id=(
                    organization.id
                ),
                secret_key=(
                    "csrf-original-secret"
                ),
            )
        )

        organization_id = (
            organization.id
        )

        subscription_id = (
            subscription.id
        )

        setup_db.commit()

        _override_authenticated_user(
            organization_id
        )

        with TestClient(
            app
        ) as client:
            response = client.get(
                "/api/alerts/webhooks",
                headers=AUTH_HEADERS,
            )

            assert (
                response.status_code
                == 200
            ), response.text

            response = client.post(
                "/api/alerts/webhooks",
                headers=AUTH_HEADERS,
                json={
                    "url": (
                        "https://hooks.example.test/new"
                    ),
                    "events": [
                        "pattern"
                    ],
                },
            )

            assert (
                response.status_code
                == 403
            ), response.text

            response = client.post(
                (
                    "/api/alerts/webhooks/"
                    f"{subscription_id}"
                    "/rotate-secret"
                ),
                headers=AUTH_HEADERS,
            )

            assert (
                response.status_code
                == 403
            ), response.text

            response = client.delete(
                (
                    "/api/alerts/webhooks/"
                    f"{subscription_id}"
                ),
                headers=AUTH_HEADERS,
            )

            assert (
                response.status_code
                == 403
            ), response.text

        verify_db = SessionLocal()

        try:
            rows = (
                verify_db.query(
                    WebhookSubscription
                )
                .filter(
                    WebhookSubscription.organization_id
                    == organization_id
                )
                .all()
            )

            assert len(
                rows
            ) == 1

            stored = rows[0]

            assert (
                stored.id
                == subscription_id
            )

            assert (
                decrypt_webhook_secret(
                    EncryptedWebhookSecret(
                        version=(
                            stored.secret_encryption_version
                        ),
                        nonce=stored.secret_nonce,
                        ciphertext=(
                            stored.secret_ciphertext
                        ),
                    ),
                    organization_id=organization_id,
                    subscription_id=subscription_id,
                    encoded_encryption_key=(
                        WEBHOOK_SECRET_ENCRYPTION_KEY
                    ),
                )
                == "csrf-original-secret"
            )

            assert (
                stored.is_active
                is True
            )
        finally:
            verify_db.close()

    finally:
        _clear_overrides()

        setup_db.close()

        _cleanup(
            organization_ids
        )