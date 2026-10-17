from datetime import (
    datetime,
    timedelta,
    timezone,
)
import uuid

import pytest

from src.services.pattern_evidence_service import (
    PatternEvidenceValidationError,
    _as_uuid,
    _normalize_cursor,
    _validate_lookback_days,
)


def test_as_uuid_accepts_uuid():
    value = uuid.uuid4()

    assert (
        _as_uuid(
            value,
            field_name="organization_id",
        )
        == value
    )


def test_as_uuid_accepts_string():
    value = uuid.uuid4()

    assert (
        _as_uuid(
            str(value),
            field_name="organization_id",
        )
        == value
    )


def test_as_uuid_rejects_invalid_value():
    with pytest.raises(
        PatternEvidenceValidationError
    ):
        _as_uuid(
            "not-a-uuid",
            field_name="organization_id",
        )


def test_normalize_cursor_preserves_utc():
    value = datetime(
        2026,
        8,
        31,
        2,
        0,
        tzinfo=timezone.utc,
    )

    assert (
        _normalize_cursor(
            value
        )
        == value
    )


def test_normalize_cursor_treats_naive_as_utc():
    value = datetime(
        2026,
        8,
        31,
        2,
        0,
    )

    result = (
        _normalize_cursor(
            value
        )
    )

    assert result.tzinfo == timezone.utc

    assert (
        result.hour
        == 2
    )


def test_normalize_cursor_converts_offset():
    source_timezone = timezone(
        timedelta(
            hours=7
        )
    )

    value = datetime(
        2026,
        8,
        31,
        9,
        0,
        tzinfo=source_timezone,
    )

    result = (
        _normalize_cursor(
            value
        )
    )

    assert (
        result
        == datetime(
            2026,
            8,
            31,
            2,
            0,
            tzinfo=timezone.utc,
        )
    )


def test_normalize_cursor_rejects_non_datetime():
    with pytest.raises(
        PatternEvidenceValidationError
    ):
        _normalize_cursor(
            "2026-08-31"
        )


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        366,
        True,
        30.0,
        "30",
    ],
)
def test_lookback_validation_rejects_invalid(
    value,
):
    with pytest.raises(
        PatternEvidenceValidationError
    ):
        _validate_lookback_days(
            value
        )


@pytest.mark.parametrize(
    "value",
    [
        1,
        30,
        365,
    ],
)
def test_lookback_validation_accepts_range(
    value,
):
    assert (
        _validate_lookback_days(
            value
        )
        == value
    )


def test_validation_error_is_value_error():
    assert issubclass(
        PatternEvidenceValidationError,
        ValueError,
    )