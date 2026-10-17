"""
Integration tests for auth API.

Set TEST_DATABASE_URL to an isolated
PostgreSQL database before running.
"""

import os

import pytest

from fastapi.testclient import (
    TestClient
)


if not os.getenv(
    "TEST_DATABASE_URL"
):

    pytest.skip(
        "Set TEST_DATABASE_URL to run auth integration tests",
        allow_module_level=True
    )


from src.core.security import (
    CSRF_COOKIE
)
from src.main import app


client = TestClient(
    app
)


def csrf() -> str:

    response = client.get(
        "/api/auth/csrf"
    )

    assert response.status_code == 200

    token = client.cookies.get(
        CSRF_COOKIE
    )

    assert token
    assert response.json() == {
        "ok":
            True,

        "csrf_token":
            token
    }

    return token


def test_signup_requires_csrf():

    response = client.post(
        "/api/auth/signup",
        json={
            "email":
                "integration@example.com",

            "password":
                "ProductionPass!2026",

            "confirm_password":
                "ProductionPass!2026",

            "full_name":
                "Integration User",

            "terms_accepted":
                True
        }
    )

    assert response.status_code in (
        403,
        422
    )


def test_login_error_shape():

    csrf()

    token = client.cookies.get(
        CSRF_COOKIE
    )

    response = client.post(
        "/api/auth/login",
        headers={
            "X-CSRF-Token":
                token
        },
        json={
            "email":
                "missing@example.com",

            "password":
                "Wrong!Password2026"
        }
    )

    assert response.status_code == 401

    payload = response.json()

    assert (
        payload[
            "detail"
        ][
            "error"
        ][
            "code"
        ]
        ==
        "AUTH_001"
    )