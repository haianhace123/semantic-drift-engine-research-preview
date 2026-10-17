from __future__ import annotations

import uuid

import pytest

from src.core import org_utils


def test_resolve_organization_id_prefers_explicit_value(
    monkeypatch,
):
    explicit = uuid.uuid4()

    monkeypatch.setenv(
        "CRAWLER_ORGANIZATION_ID",
        str(
            uuid.uuid4()
        ),
    )

    assert (
        org_utils.resolve_organization_id(
            explicit
        )
        == explicit
    )


def test_resolve_organization_id_uses_environment(
    monkeypatch,
):
    expected = uuid.uuid4()

    monkeypatch.setenv(
        "CRAWLER_ORGANIZATION_ID",
        str(
            expected
        ),
    )

    assert (
        org_utils.resolve_organization_id()
        == expected
    )


def test_resolve_organization_id_rejects_missing_tenant(
    monkeypatch,
):
    monkeypatch.delenv(
        "CRAWLER_ORGANIZATION_ID",
        raising=False,
    )

    with pytest.raises(
        ValueError,
        match="organization is required",
    ):
        org_utils.resolve_organization_id()


def test_resolve_organization_id_rejects_invalid_uuid(
    monkeypatch,
):
    monkeypatch.setenv(
        "CRAWLER_ORGANIZATION_ID",
        "not-a-uuid",
    )

    with pytest.raises(
        ValueError,
        match="valid UUID",
    ):
        org_utils.resolve_organization_id()