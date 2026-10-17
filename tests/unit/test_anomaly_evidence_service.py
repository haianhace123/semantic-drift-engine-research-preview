from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from src.services.anomaly_evidence_service import (
    AnomalyEvidenceValidationError,
    _as_uuid,
    _normalize_cursor,
    _validate_days,
)


def _uuid(value: int) -> uuid.UUID:
    return uuid.UUID(int=value)


def test_as_uuid_accepts_uuid() -> None:
    value = _uuid(1)

    assert (
        _as_uuid(
            value,
            field_name="value",
        )
        == value
    )


def test_as_uuid_accepts_string() -> None:
    value = _uuid(2)

    assert (
        _as_uuid(
            str(value),
            field_name="value",
        )
        == value
    )


def test_as_uuid_rejects_invalid_value() -> None:
    with pytest.raises(
        AnomalyEvidenceValidationError,
        match="value must be a valid UUID",
    ):
        _as_uuid(
            "not-a-uuid",
            field_name="value",
        )


def test_normalize_cursor_converts_naive_to_utc() -> None:
    value = datetime(
        2026,
        9,
        1,
        8,
        0,
    )

    normalized = _normalize_cursor(
        value
    )

    assert normalized.tzinfo == timezone.utc
    assert normalized.hour == 8


def test_normalize_cursor_converts_aware_to_utc() -> None:
    local_timezone = timezone.utc

    value = datetime(
        2026,
        9,
        1,
        8,
        0,
        tzinfo=local_timezone,
    )

    normalized = _normalize_cursor(
        value
    )

    assert normalized == value
    assert normalized.tzinfo == timezone.utc


def test_normalize_cursor_rejects_non_datetime() -> None:
    with pytest.raises(
        AnomalyEvidenceValidationError,
        match="as_of must be a datetime",
    ):
        _normalize_cursor(
            "2026-09-01"
        )


@pytest.mark.parametrize(
    "value",
    (
        1,
        7,
        28,
        365,
    ),
)
def test_validate_days_accepts_valid_integer(
    value,
) -> None:
    assert (
        _validate_days(
            value,
            field_name="days",
        )
        == value
    )


@pytest.mark.parametrize(
    (
        "value",
        "message",
    ),
    (
        (
            True,
            "must be an integer",
        ),
        (
            1.5,
            "must be an integer",
        ),
        (
            "7",
            "must be an integer",
        ),
        (
            0,
            "must be at least 1",
        ),
        (
            -1,
            "must be at least 1",
        ),
        (
            366,
            "must not exceed 365",
        ),
    ),
)
def test_validate_days_rejects_invalid_values(
    value,
    message,
) -> None:
    with pytest.raises(
        AnomalyEvidenceValidationError,
        match=message,
    ):
        _validate_days(
            value,
            field_name="days",
        )