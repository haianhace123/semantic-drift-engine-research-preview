from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from src.core.database import SessionLocal
from src.core.security import (
    CSRF_COOKIE,
    hash_password,
)
from src.main import app
from src.models import (
    Organization,
    User,
)
from src.models.worker_operational_state import (
    WORKER_KINDS,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


TEST_PASSWORD = "WorkerOperations!123"


def _identity(
    prefix: str,
):
    suffix = uuid.uuid4().hex[:12]

    return {
        "slug": f"{prefix}-{suffix}",
        "email": (
            f"{prefix}-{suffix}"
            "@example.test"
        ),
    }


def _create_user(
    *,
    prefix: str,
    role: str,
):
    identity = _identity(
        prefix
    )

    db = SessionLocal()

    try:
        organization = Organization(
            name=f"Worker operations {prefix}",
            slug=identity["slug"],
            billing_email=identity["email"],
            subscription_tier="free",
            quota_urls=10,
            quota_requests=1000,
            is_active=True,
        )

        db.add(
            organization
        )
        db.flush()

        user = User(
            organization_id=organization.id,
            email=identity["email"],
            password_hash=hash_password(
                TEST_PASSWORD
            ),
            full_name="Worker Operations Integration",
            role=role,
            is_active=True,
            email_verified=True,
            login_attempts=0,
        )

        db.add(
            user
        )
        db.commit()

        return (
            organization.id,
            user.email,
        )

    finally:
        db.close()


def _cleanup_organization(
    organization_id,
) -> None:
    db = SessionLocal()

    try:
        (
            db.query(
                Organization
            )
            .filter(
                Organization.id
                == organization_id
            )
            .delete(
                synchronize_session=False
            )
        )

        db.commit()

    finally:
        db.close()


def _csrf(
    client: TestClient,
) -> str:
    response = client.get(
        "/api/auth/csrf"
    )

    assert (
        response.status_code
        == 200
    )

    token = client.cookies.get(
        CSRF_COOKIE
    )

    assert token

    return token


def _login(
    client: TestClient,
    *,
    email: str,
) -> None:
    csrf_token = _csrf(
        client
    )

    response = client.post(
        "/api/auth/login",
        headers={
            "X-CSRF-Token":
                csrf_token,
        },
        json={
            "email": email,
            "password": TEST_PASSWORD,
        },
    )

    assert (
        response.status_code
        == 200
    ), response.text


def test_worker_operations_requires_authentication():
    with TestClient(
        app
    ) as client:
        response = client.get(
            "/api/operations/workers"
        )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "role",
    [
        "user",
        "viewer",
    ],
)
def test_worker_operations_requires_admin(
    role: str,
):
    (
        organization_id,
        email,
    ) = _create_user(
        prefix=f"worker-operations-{role}",
        role=role,
    )

    client = TestClient(
        app
    )

    try:
        _login(
            client,
            email=email,
        )

        response = client.get(
            "/api/operations/workers"
        )

        assert response.status_code == 403

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_admin_can_read_worker_operations():
    (
        organization_id,
        email,
    ) = _create_user(
        prefix="worker-operations-admin",
        role="admin",
    )

    client = TestClient(
        app
    )

    try:
        _login(
            client,
            email=email,
        )

        response = client.get(
            "/api/operations/workers"
        )

        assert response.status_code == 200

        payload = response.json()

        assert (
            payload[
                "heartbeat_stale_after_seconds"
            ]
            == 30
        )
        assert (
            payload[
                "concern_window_seconds"
            ]
            == 300
        )

        workers = payload[
            "workers"
        ]

        assert {
            worker["worker_kind"]
            for worker in workers
        } == set(
            WORKER_KINDS
        )

        assert len(
            workers
        ) == len(
            WORKER_KINDS
        )

        for worker in workers:
            assert worker["status"] in {
                "healthy",
                "degraded",
                "unavailable",
                "disabled",
            }
            assert isinstance(worker["expected"], bool)
            if worker["worker_kind"] != "email":
                assert worker["expected"] is True

            assert (
                worker[
                    "fresh_instance_count"
                ]
                >= 0
            )
            assert (
                worker[
                    "degraded_instance_count"
                ]
                >= 0
            )
            assert (
                worker[
                    "recent_stale_instance_count"
                ]
                >= 0
            )

            for instance in worker[
                "instances"
            ]:
                assert instance["status"] in {
                    "healthy",
                    "degraded",
                    "stale",
                }

                assert (
                    "worker_instance_id"
                    in instance
                )
                assert (
                    "last_heartbeat_at"
                    in instance
                )
                assert (
                    "consecutive_failures"
                    in instance
                )

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_capabilities_requires_authentication():
    with TestClient(app) as client:
        response = client.get("/api/operations/capabilities")
    assert response.status_code == 401


@pytest.mark.parametrize("role", ["user", "viewer"])
def test_capabilities_requires_admin(role: str):
    organization_id, email = _create_user(
        prefix=f"capabilities-{role}", role=role,
    )
    with TestClient(app) as client:
        try:
            _login(client, email=email)
            response = client.get("/api/operations/capabilities")
            assert response.status_code == 403
        finally:
            _cleanup_organization(organization_id)


def test_admin_can_read_capabilities_without_provider_secrets(monkeypatch):
    organization_id, email = _create_user(
        prefix="capabilities-admin", role="admin",
    )
    monkeypatch.setenv("VECTOR_WORKER_EMBEDDINGS_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "do-not-reveal-this-key")
    with TestClient(app) as client:
        try:
            _login(client, email=email)
            response = client.get("/api/operations/capabilities")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["basis"] == "api_configuration_and_worker_heartbeat"
            assert payload["embedding_provider"] == "gemini"
            assert payload["worker_status"] in {"healthy", "degraded", "unavailable"}
            capabilities = {item["name"]: item for item in payload["capabilities"]}
            assert set(capabilities) == {
                "embedding_generation", "vector_analysis", "risk_scoring",
                "pattern_detection", "anomaly_detection",
            }
            assert capabilities["embedding_generation"]["requested"] is True
            assert "do-not-reveal-this-key" not in response.text
            assert "GEMINI_API_KEY" not in response.text
        finally:
            _cleanup_organization(organization_id)
