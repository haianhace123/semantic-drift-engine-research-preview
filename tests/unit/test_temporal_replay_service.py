from __future__ import annotations

import uuid

from datetime import datetime, timedelta, timezone

import pytest

from src.services.temporal_replay_service import (
    TemporalReplayValidationError,
    _as_uuid,
    _normalize_cursor,
)


def test_as_uuid_accepts_uuid() -> None:
    value = uuid.uuid4()

    assert _as_uuid(
        value,
        field_name="organization_id",
    ) == value


def test_as_uuid_accepts_uuid_string() -> None:
    value = uuid.uuid4()

    result = _as_uuid(
        str(value),
        field_name="organization_id",
    )

    assert result == value
    assert isinstance(result, uuid.UUID)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "not-a-uuid",
        123,
        object(),
    ],
)
def test_as_uuid_rejects_invalid_values(
    value,
) -> None:
    with pytest.raises(
        TemporalReplayValidationError,
        match="organization_id must be a valid UUID",
    ):
        _as_uuid(
            value,
            field_name="organization_id",
        )


def test_normalize_cursor_preserves_utc() -> None:
    cursor = datetime(
        2026,
        8,
        30,
        12,
        30,
        45,
        tzinfo=timezone.utc,
    )

    result = _normalize_cursor(cursor)

    assert result == cursor
    assert result.tzinfo == timezone.utc


def test_normalize_cursor_treats_naive_datetime_as_utc() -> None:
    cursor = datetime(
        2026,
        8,
        30,
        12,
        30,
        45,
    )

    result = _normalize_cursor(cursor)

    assert result == datetime(
        2026,
        8,
        30,
        12,
        30,
        45,
        tzinfo=timezone.utc,
    )


def test_normalize_cursor_converts_offset_to_utc() -> None:
    vietnam_timezone = timezone(
        timedelta(hours=7)
    )

    cursor = datetime(
        2026,
        8,
        30,
        16,
        30,
        0,
        tzinfo=vietnam_timezone,
    )

    result = _normalize_cursor(cursor)

    assert result == datetime(
        2026,
        8,
        30,
        9,
        30,
        0,
        tzinfo=timezone.utc,
    )

    assert result.tzinfo == timezone.utc


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "2026-08-30T09:30:00Z",
        123,
        object(),
    ],
)
def test_normalize_cursor_rejects_non_datetime(
    value,
) -> None:
    with pytest.raises(
        TemporalReplayValidationError,
        match="cursor must be a datetime",
    ):
        _normalize_cursor(value)


def test_validation_error_is_value_error() -> None:
    assert issubclass(
        TemporalReplayValidationError,
        ValueError,
    )