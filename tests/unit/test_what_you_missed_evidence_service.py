from datetime import (
    datetime,
    timedelta,
    timezone,
)
import uuid

import pytest

from src.services.what_you_missed_evidence_service import (
    WhatYouMissedEvidenceBundle,
    WhatYouMissedEvidenceValidationError,
    _as_uuid,
    _normalize_datetime,
    _normalize_pattern_event_ids,
    _normalize_window,
    _optional_float,
)


BASE = datetime(
    2026,
    9,
    2,
    1,
    0,
    tzinfo=timezone.utc,
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


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "not-a-uuid",
        123,
    ],
)
def test_as_uuid_rejects_invalid_value(
    value,
):
    with pytest.raises(
        WhatYouMissedEvidenceValidationError
    ):
        _as_uuid(
            value,
            field_name="organization_id",
        )


def test_normalize_datetime_preserves_utc():
    result = _normalize_datetime(
        BASE,
        field_name="until",
    )

    assert result == BASE


def test_normalize_datetime_treats_naive_as_utc():
    value = datetime(
        2026,
        9,
        2,
        1,
        0,
    )

    result = _normalize_datetime(
        value,
        field_name="until",
    )

    assert result == BASE
    assert result.tzinfo == timezone.utc


def test_normalize_datetime_converts_offset_to_utc():
    source_timezone = timezone(
        timedelta(
            hours=7
        )
    )

    value = datetime(
        2026,
        9,
        2,
        8,
        0,
        tzinfo=source_timezone,
    )

    result = _normalize_datetime(
        value,
        field_name="until",
    )

    assert result == BASE


@pytest.mark.parametrize(
    "value",
    [
        None,
        "2026-09-02",
        123,
        1.5,
    ],
)
def test_normalize_datetime_rejects_non_datetime(
    value,
):
    with pytest.raises(
        WhatYouMissedEvidenceValidationError
    ):
        _normalize_datetime(
            value,
            field_name="until",
        )


def test_normalize_window_returns_utc_window():
    source_timezone = timezone(
        timedelta(
            hours=7
        )
    )

    since = datetime(
        2026,
        9,
        2,
        7,
        0,
        tzinfo=source_timezone,
    )

    until = datetime(
        2026,
        9,
        2,
        9,
        0,
        tzinfo=source_timezone,
    )

    normalized_since, normalized_until = (
        _normalize_window(
            since=since,
            until=until,
        )
    )

    assert normalized_since == datetime(
        2026,
        9,
        2,
        0,
        0,
        tzinfo=timezone.utc,
    )

    assert normalized_until == datetime(
        2026,
        9,
        2,
        2,
        0,
        tzinfo=timezone.utc,
    )


def test_normalize_window_rejects_equal_bounds():
    with pytest.raises(
        WhatYouMissedEvidenceValidationError
    ):
        _normalize_window(
            since=BASE,
            until=BASE,
        )


def test_normalize_window_rejects_reverse_bounds():
    with pytest.raises(
        WhatYouMissedEvidenceValidationError
    ):
        _normalize_window(
            since=BASE,
            until=(
                BASE
                - timedelta(
                    seconds=1
                )
            ),
        )


def test_optional_float_returns_none():
    assert (
        _optional_float(
            None
        )
        is None
    )


@pytest.mark.parametrize(
    (
        "value",
        "expected",
    ),
    [
        (
            0,
            0.0,
        ),
        (
            1,
            1.0,
        ),
        (
            0.25,
            0.25,
        ),
        (
            "0.75",
            0.75,
        ),
    ],
)
def test_optional_float_converts_value(
    value,
    expected,
):
    assert (
        _optional_float(
            value
        )
        == expected
    )


def test_normalize_pattern_event_ids_accepts_uuid_and_string():
    first = uuid.uuid4()
    second = uuid.uuid4()
    pattern_id = uuid.uuid4()

    result = (
        _normalize_pattern_event_ids(
            [
                first,
                str(second),
            ],
            pattern_id=pattern_id,
        )
    )

    assert result == (
        first,
        second,
    )


def test_normalize_pattern_event_ids_preserves_order():
    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()

    result = (
        _normalize_pattern_event_ids(
            [
                str(first),
                str(second),
                str(third),
            ],
            pattern_id=uuid.uuid4(),
        )
    )

    assert result == (
        first,
        second,
        third,
    )


def test_normalize_pattern_event_ids_none_is_empty():
    assert (
        _normalize_pattern_event_ids(
            None,
            pattern_id=uuid.uuid4(),
        )
        == tuple()
    )


@pytest.mark.parametrize(
    "value",
    [
        "not-a-list",
        {},
        123,
    ],
)
def test_normalize_pattern_event_ids_rejects_non_sequence(
    value,
):
    with pytest.raises(
        WhatYouMissedEvidenceValidationError
    ):
        _normalize_pattern_event_ids(
            value,
            pattern_id=uuid.uuid4(),
        )


def test_normalize_pattern_event_ids_rejects_invalid_uuid():
    with pytest.raises(
        WhatYouMissedEvidenceValidationError
    ):
        _normalize_pattern_event_ids(
            [
                str(
                    uuid.uuid4()
                ),
                "bad-event-id",
            ],
            pattern_id=uuid.uuid4(),
        )


def test_bundle_count_properties():
    bundle = WhatYouMissedEvidenceBundle(
        organization_id=uuid.uuid4(),
        since=BASE,
        until=(
            BASE
            + timedelta(
                hours=1
            )
        ),
        events=tuple(),
        risks=tuple(),
        topology=tuple(),
        patterns=tuple(),
        anomalies=tuple(),
    )

    assert bundle.event_count == 0
    assert bundle.risk_count == 0
    assert bundle.topology_count == 0
    assert bundle.pattern_count == 0
    assert bundle.anomaly_count == 0


def test_validation_error_is_value_error():
    assert issubclass(
        WhatYouMissedEvidenceValidationError,
        ValueError,
    )