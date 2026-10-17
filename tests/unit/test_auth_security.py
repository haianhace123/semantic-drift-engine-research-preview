import pytest

from src.api.auth_routes import (
    validate_email,
    validate_password
)

from src.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password
)


def test_bcrypt_password_roundtrip():

    password = (
        "ProductionPass!2026"
    )

    hashed = hash_password(
        password
    )

    assert hashed.startswith(
        "$2b$12$"
    )

    assert verify_password(
        password,
        hashed
    )

    assert not verify_password(
        "WrongPass!2026",
        hashed
    )


def test_access_token_has_short_expiry_claims():

    session_id = (
        "11111111-1111-1111-1111-111111111111"
    )

    token = create_access_token(
        "123",
        session_id=session_id,
    )

    payload = decode_token(
        token,
        "access"
    )

    assert payload["sub"] == "123"

    assert payload["sid"] == session_id

    assert payload["type"] == "access"

    assert (
        payload["exp"]
        -
        payload["iat"]
        ==
        15 * 60
    )


def test_refresh_token_defaults_to_non_persistent():

    token, expires_at = create_refresh_token(
        "123"
    )

    payload = decode_token(
        token,
        "refresh"
    )

    assert payload["sub"] == "123"

    assert payload["type"] == "refresh"

    assert (
        payload["remember_me"]
        is False
    )

    assert payload["jti"]

    assert (
        int(
            expires_at.timestamp()
        )
        ==
        payload["exp"]
    )


def test_refresh_token_preserves_remember_me():

    token, expires_at = create_refresh_token(
        "123",
        remember_me=True,
    )

    payload = decode_token(
        token,
        "refresh"
    )

    assert payload["sub"] == "123"

    assert payload["type"] == "refresh"

    assert (
        payload["remember_me"]
        is True
    )

    assert payload["jti"]

    assert (
        int(
            expires_at.timestamp()
        )
        ==
        payload["exp"]
    )


def test_email_validation():

    assert (
        validate_email(
            "User@Example.com"
        )
        ==
        "user@example.com"
    )

    with pytest.raises(Exception):
        validate_email(
            "bad-email"
        )


def test_password_strength():

    validate_password(
        "ProductionPass!2026"
    )

    with pytest.raises(Exception):
        validate_password(
            "weak"
        )