from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import HTTPException

from src.core import security
from src.models import ApiKey


class FakeQuery:
    def __init__(
        self,
        result,
    ):
        self.result = result

    def join(self, *args, **kwargs):
        return self

    def filter(
        self,
        *args,
        **kwargs,
    ):
        return self

    def first(
        self,
    ):
        return self.result


class FakeDB:
    def __init__(
        self,
        result=None,
    ):
        self.result = result
        self.commit_count = 0

    def query(
        self,
        model,
    ):
        assert model is ApiKey

        return FakeQuery(
            self.result
        )

    def commit(
        self,
    ):
        self.commit_count += 1


def _api_key(
    *,
    active=True,
    expires_at=None,
    permissions=None,
):
    return ApiKey(
        name="test-key",
        key_hash=security.token_hash(
            "sk-test"
        ),
        permissions=(
            permissions
            if permissions
            is not None
            else {
                "read": True,
                "write": False,
            }
        ),
        is_active=active,
        expires_at=expires_at,
    )


def test_verify_api_key_accepts_active_key():
    key = _api_key()

    db = FakeDB(
        key
    )

    result = (
        security.verify_api_key(
            "sk-test",
            db,
        )
    )

    assert result is key


def test_verify_api_key_rejects_missing_key():
    db = FakeDB()

    assert (
        security.verify_api_key(
            "",
            db,
        )
        is None
    )


def test_verify_api_key_rejects_unknown_key():
    db = FakeDB(
        None
    )

    assert (
        security.verify_api_key(
            "sk-invalid",
            db,
        )
        is None
    )


def test_verify_api_key_rejects_expired_key():
    key = _api_key(
        expires_at=(
            security.now_utc()
            - timedelta(
                seconds=1
            )
        )
    )

    db = FakeDB(
        key
    )

    assert (
        security.verify_api_key(
            "sk-test",
            db,
        )
        is None
    )


def test_authenticate_api_key_updates_last_used():
    key = _api_key()

    db = FakeDB(
        key
    )

    assert (
        key.last_used_at
        is None
    )

    result = (
        security.authenticate_api_key(
            api_key="sk-test",
            db=db,
        )
    )

    assert result is key

    assert (
        key.last_used_at
        is not None
    )

    assert (
        db.commit_count
        == 1
    )


def test_authenticate_api_key_rejects_missing_key():
    db = FakeDB()

    with pytest.raises(
        HTTPException
    ) as exc:
        security.authenticate_api_key(
            api_key=None,
            db=db,
        )

    assert (
        exc.value.status_code
        == 401
    )


def test_read_permission_is_allowed():
    key = _api_key(
        permissions={
            "read": True,
            "write": False,
        }
    )

    dependency = (
        security.require_api_key_permission(
            "read"
        )
    )

    assert (
        dependency(
            api_key=key
        )
        is key
    )


def test_write_permission_is_denied():
    key = _api_key(
        permissions={
            "read": True,
            "write": False,
        }
    )

    dependency = (
        security.require_api_key_permission(
            "write"
        )
    )

    with pytest.raises(
        HTTPException
    ) as exc:
        dependency(
            api_key=key
        )

    assert (
        exc.value.status_code
        == 403
    )