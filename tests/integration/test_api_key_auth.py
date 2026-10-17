from __future__ import annotations

import os
import uuid

import pytest

from fastapi import (
    Depends,
    FastAPI,
)
from fastapi.testclient import (
    TestClient,
)

from src.core.database import (
    SessionLocal,
)
from src.core.security import (
    CSRF_COOKIE,
    authenticate_api_key,
    hash_password,
    require_api_key_permission,
)
from src.main import app
from src.models import (
    ApiKey,
    Organization,
    User,
)


RUN_API_KEY_INTEGRATION_TESTS = (
    os.getenv(
        "RUN_API_KEY_INTEGRATION_TESTS",
        "",
    )
    == "1"
)


pytestmark = pytest.mark.skipif(
    not RUN_API_KEY_INTEGRATION_TESTS,
    reason=(
        "Set RUN_API_KEY_INTEGRATION_TESTS=1 "
        "to run API-key integration tests."
    ),
)


TEST_PASSWORD = (
    "ApiKeyIntegration!123"
)


def _identity(
    prefix: str,
):
    suffix = uuid.uuid4().hex[:12]

    return {
        "slug": (
            f"{prefix}-{suffix}"
        ),
        "email": (
            f"{prefix}-{suffix}"
            "@example.test"
        ),
    }


def _create_user(
    *,
    prefix: str,
    role: str = "admin",
):
    identity = _identity(
        prefix
    )

    db = SessionLocal()

    try:
        organization = Organization(
            name=(
                f"API key test {prefix}"
            ),
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
            full_name="API Key Integration",
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
            user.id,
            user.email,
        )

    finally:
        db.close()


def _cleanup_organization(
    organization_id,
):
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

    except Exception:
        db.rollback()
        raise

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
            "email":
                email,
            "password":
                TEST_PASSWORD,
        },
    )

    assert (
        response.status_code
        == 200
    ), response.text


def _create_key(
    client: TestClient,
    *,
    name: str,
    read: bool = True,
    write: bool = False,
):
    csrf_token = client.cookies.get(
        CSRF_COOKIE
    )

    assert csrf_token

    response = client.post(
        "/api/auth/api-keys",
        headers={
            "X-CSRF-Token":
                csrf_token,
        },
        json={
            "name":
                name,
            "permissions": {
                "read":
                    read,
                "write":
                    write,
            },
        },
    )

    assert (
        response.status_code
        == 200
    ), response.text

    payload = (
        response.json()
    )

    assert payload["key"].startswith(
        "sk-"
    )

    return payload


def _api_key_row(
    key_id,
):
    db = SessionLocal()

    try:
        return (
            db.query(
                ApiKey
            )
            .filter(
                ApiKey.id
                == key_id
            )
            .first()
        )

    finally:
        db.close()


# ============================================================
# PROGRAMMATIC TEST APPLICATION
#
# These endpoints exist only inside this integration test and
# exercise the production API-key dependencies directly.
# ============================================================

programmatic_app = FastAPI()


@programmatic_app.get(
    "/read"
)
def api_key_read(
    api_key: ApiKey = Depends(
        require_api_key_permission(
            "read"
        )
    ),
):
    return {
        "organization_id":
            str(
                api_key.organization_id
            ),
        "key_id":
            str(
                api_key.id
            ),
    }


@programmatic_app.post(
    "/write"
)
def api_key_write(
    api_key: ApiKey = Depends(
        require_api_key_permission(
            "write"
        )
    ),
):
    return {
        "organization_id":
            str(
                api_key.organization_id
            ),
    }


@programmatic_app.get(
    "/authenticated"
)
def api_key_authenticated(
    api_key: ApiKey = Depends(
        authenticate_api_key
    ),
):
    return {
        "key_id":
            str(
                api_key.id
            ),
    }


def test_api_key_lifecycle_permissions_and_revoke():
    (
        organization_id,
        _user_id,
        email,
    ) = _create_user(
        prefix="api-key-lifecycle"
    )

    browser = TestClient(
        app
    )

    programmatic = TestClient(
        programmatic_app
    )

    try:
        _login(
            browser,
            email=email,
        )

        created = _create_key(
            browser,
            name="Read-only integration key",
            read=True,
            write=False,
        )

        key_id = uuid.UUID(
            created["id"]
        )

        raw_key = created["key"]

        # ----------------------------------------------------
        # Raw key must not be stored in DB.
        # ----------------------------------------------------

        row = _api_key_row(
            key_id
        )

        assert row is not None

        assert (
            row.key_hash
            != raw_key
        )

        assert (
            raw_key
            not in row.key_hash
        )

        # ----------------------------------------------------
        # List endpoint must never expose raw key/hash.
        # ----------------------------------------------------

        response = browser.get(
            "/api/auth/api-keys"
        )

        assert (
            response.status_code
            == 200
        )

        serialized = (
            response.text
        )

        assert (
            raw_key
            not in serialized
        )

        assert (
            row.key_hash
            not in serialized
        )

        # ----------------------------------------------------
        # Valid read permission authenticates.
        # ----------------------------------------------------

        response = programmatic.get(
            "/read",
            headers={
                "X-API-Key":
                    raw_key,
            },
        )

        assert (
            response.status_code
            == 200
        ), response.text

        payload = response.json()

        assert (
            payload[
                "organization_id"
            ]
            == str(
                organization_id
            )
        )

        # ----------------------------------------------------
        # Read-only key cannot write.
        # ----------------------------------------------------

        response = programmatic.post(
            "/write",
            headers={
                "X-API-Key":
                    raw_key,
            },
        )

        assert (
            response.status_code
            == 403
        )

        # ----------------------------------------------------
        # Authentication updates last_used_at.
        # ----------------------------------------------------

        row = _api_key_row(
            key_id
        )

        assert (
            row.last_used_at
            is not None
        )

        # ----------------------------------------------------
        # Revoke requires CSRF.
        # ----------------------------------------------------

        response = browser.delete(
            f"/api/auth/api-keys/{key_id}"
        )

        assert (
            response.status_code
            == 403
        )

        # ----------------------------------------------------
        # Revoke with CSRF succeeds.
        # ----------------------------------------------------

        csrf_token = (
            browser.cookies.get(
                CSRF_COOKIE
            )
        )

        assert csrf_token

        response = browser.delete(
            f"/api/auth/api-keys/{key_id}",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
        )

        assert (
            response.status_code
            == 200
        ), response.text

        # ----------------------------------------------------
        # Revoked key can no longer authenticate.
        # ----------------------------------------------------

        response = programmatic.get(
            "/authenticated",
            headers={
                "X-API-Key":
                    raw_key,
            },
        )

        assert (
            response.status_code
            == 401
        )

    finally:
        browser.close()
        programmatic.close()

        _cleanup_organization(
            organization_id
        )


def test_api_key_management_is_tenant_isolated():
    (
        organization_a_id,
        _user_a_id,
        email_a,
    ) = _create_user(
        prefix="api-key-tenant-a"
    )

    (
        organization_b_id,
        _user_b_id,
        email_b,
    ) = _create_user(
        prefix="api-key-tenant-b"
    )

    client_a = TestClient(
        app
    )

    client_b = TestClient(
        app
    )

    try:
        _login(
            client_a,
            email=email_a,
        )

        _login(
            client_b,
            email=email_b,
        )

        created = _create_key(
            client_a,
            name="Tenant A key",
        )

        key_id = created["id"]

        # ----------------------------------------------------
        # Tenant B cannot see tenant A's key.
        # ----------------------------------------------------

        response = client_b.get(
            "/api/auth/api-keys"
        )

        assert (
            response.status_code
            == 200
        )

        tenant_b_ids = {
            item["id"]
            for item
            in response.json()["data"]
        }

        assert (
            key_id
            not in tenant_b_ids
        )

        # ----------------------------------------------------
        # Tenant B cannot revoke tenant A's key.
        # ----------------------------------------------------

        csrf_b = (
            client_b.cookies.get(
                CSRF_COOKIE
            )
        )

        assert csrf_b

        response = client_b.delete(
            f"/api/auth/api-keys/{key_id}",
            headers={
                "X-CSRF-Token":
                    csrf_b,
            },
        )

        assert (
            response.status_code
            == 404
        )

        # ----------------------------------------------------
        # Tenant A key remains active.
        # ----------------------------------------------------

        db = SessionLocal()

        try:
            row = (
                db.query(
                    ApiKey
                )
                .filter(
                    ApiKey.id
                    == uuid.UUID(
                        key_id
                    )
                )
                .first()
            )

            assert row is not None

            assert (
                row.organization_id
                == organization_a_id
            )

            assert (
                row.organization_id
                != organization_b_id
            )

            assert (
                row.is_active
                is True
            )

        finally:
            db.close()

    finally:
        client_a.close()
        client_b.close()

        _cleanup_organization(
            organization_a_id
        )

        _cleanup_organization(
            organization_b_id
        )


@pytest.mark.parametrize("role", ["user", "viewer"])
def test_api_key_management_requires_admin(role: str):
    (
        organization_id,
        _user_id,
        email,
    ) = _create_user(
        prefix=f"api-key-rbac-{role}",
        role=role,
    )

    client = TestClient(app)

    try:
        _login(
            client,
            email=email,
        )

        csrf_token = client.cookies.get(
            CSRF_COOKIE
        )

        assert csrf_token

        create_response = client.post(
            "/api/auth/api-keys",
            headers={
                "X-CSRF-Token": csrf_token,
            },
            json={
                "name": "Unauthorized credential",
                "permissions": {
                    "read": True,
                    "write": False,
                },
            },
        )

        assert create_response.status_code == 403

        list_response = client.get(
            "/api/auth/api-keys"
        )

        assert list_response.status_code == 403

        revoke_response = client.delete(
            f"/api/auth/api-keys/{uuid.uuid4()}",
            headers={
                "X-CSRF-Token": csrf_token,
            },
        )

        assert revoke_response.status_code == 403

        db = SessionLocal()

        try:
            assert (
                db.query(ApiKey)
                .filter(
                    ApiKey.organization_id == organization_id
                )
                .count()
                == 0
            )
        finally:
            db.close()

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )

def test_personal_signup_creates_admin_owner(monkeypatch):
    from src.api import auth_routes as auth_module

    # Email delivery is outside this test's authority.
    monkeypatch.setattr(auth_module, "enqueue_email", lambda **_kwargs: None)
    identity = _identity("personal-admin-signup")
    browser = TestClient(app)
    downstream = TestClient(programmatic_app)
    organization_id = None

    try:
        csrf_token = _csrf(browser)
        response = browser.post(
            "/api/auth/signup",
            headers={"X-CSRF-Token": csrf_token},
            json={
                "email": identity["email"],
                "password": TEST_PASSWORD,
                "confirm_password": TEST_PASSWORD,
                "full_name": "Personal Organization Owner",
                "terms_accepted": True,
            },
        )
        assert response.status_code == 200, response.text

        with SessionLocal() as db:
            user = db.query(User).filter(User.email == identity["email"]).one()
            organization_id = user.organization_id
            assert user.role == "admin"
            assert user.is_active is True
            assert db.query(User).filter(User.organization_id == organization_id).count() == 1
            user.email_verified = True
            db.commit()

        _login(browser, email=identity["email"])
        created = _create_key(browser, name="Personal owner read-only API key")
        read_response = downstream.get(
            "/read", headers={"X-API-Key": created["key"]}
        )
        assert read_response.status_code == 200, read_response.text
        assert read_response.json()["organization_id"] == str(organization_id)
        write_response = downstream.post(
            "/write", headers={"X-API-Key": created["key"]}
        )
        assert write_response.status_code == 403
    finally:
        browser.close()
        downstream.close()
        if organization_id is not None:
            _cleanup_organization(organization_id)


def test_new_oauth_personal_organization_creates_admin_owner():
    import asyncio
    from starlette.requests import Request
    from src.api.auth_routes import finish_oauth

    identity = _identity("personal-admin-oauth")
    organization_id = None
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/auth/google/callback",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
    })

    try:
        with SessionLocal() as db:
            response = asyncio.run(finish_oauth(
                provider="google",
                provider_id="personal-admin-" + uuid.uuid4().hex,
                email=identity["email"],
                full_name="OAuth Personal Owner",
                request=request,
                db=db,
            ))
            assert response.status_code == 303
            user = db.query(User).filter(User.email == identity["email"]).one()
            organization_id = user.organization_id
            assert user.role == "admin"
            assert user.email_verified is True
            assert db.query(User).filter(User.organization_id == organization_id).count() == 1
    finally:
        if organization_id is not None:
            _cleanup_organization(organization_id)
