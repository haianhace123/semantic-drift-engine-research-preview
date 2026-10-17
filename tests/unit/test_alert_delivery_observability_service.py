from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.services.alert_delivery_observability_service import (
    get_alert_delivery_operational_summary,
)


NOW = datetime(
    2026,
    9,
    13,
    5,
    0,
    tzinfo=timezone.utc,
)


def _summary_row(
    **overrides,
):
    values = {
        "total_count": 0,
        "pending_count": 0,
        "processing_count": 0,
        "retryable_count": 0,
        "failed_count": 0,
        "delivered_count": 0,
        "claimable_count": 0,
        "stale_processing_count": 0,
        "exhausted_count": 0,
        "total_attempt_count": 0,
        "oldest_pending_at": None,
        "oldest_retryable_at": None,
        "oldest_processing_claimed_at": None,
    }
    values.update(
        overrides
    )
    return SimpleNamespace(
        **values
    )


def _db_returning(
    row,
):
    db = MagicMock()

    query = db.query.return_value
    filtered = query.filter.return_value
    filtered.one.return_value = row

    return db


def test_summary_maps_aggregate_row():
    organization_id = uuid.uuid4()

    oldest_pending_at = (
        NOW
        - timedelta(
            minutes=20
        )
    )
    oldest_retryable_at = (
        NOW
        - timedelta(
            minutes=10
        )
    )
    oldest_processing_claimed_at = (
        NOW
        - timedelta(
            minutes=5
        )
    )

    db = _db_returning(
        _summary_row(
            total_count=15,
            pending_count=3,
            processing_count=2,
            retryable_count=4,
            failed_count=1,
            delivered_count=5,
            claimable_count=6,
            stale_processing_count=1,
            exhausted_count=2,
            total_attempt_count=27,
            oldest_pending_at=(
                oldest_pending_at
            ),
            oldest_retryable_at=(
                oldest_retryable_at
            ),
            oldest_processing_claimed_at=(
                oldest_processing_claimed_at
            ),
        )
    )

    summary = (
        get_alert_delivery_operational_summary(
            db,
            organization_id=organization_id,
            now=NOW,
        )
    )

    assert (
        summary.organization_id
        == organization_id
    )
    assert summary.observed_at == NOW

    assert summary.total_count == 15

    assert summary.pending_count == 3
    assert summary.processing_count == 2
    assert summary.retryable_count == 4
    assert summary.failed_count == 1
    assert summary.delivered_count == 5

    assert summary.claimable_count == 6
    assert (
        summary.stale_processing_count
        == 1
    )
    assert summary.exhausted_count == 2

    assert (
        summary.total_attempt_count
        == 27
    )

    assert (
        summary.oldest_pending_at
        == oldest_pending_at
    )
    assert (
        summary.oldest_retryable_at
        == oldest_retryable_at
    )
    assert (
        summary.oldest_processing_claimed_at
        == oldest_processing_claimed_at
    )


def test_summary_normalizes_null_aggregates():
    organization_id = uuid.uuid4()

    db = _db_returning(
        _summary_row(
            total_count=None,
            pending_count=None,
            processing_count=None,
            retryable_count=None,
            failed_count=None,
            delivered_count=None,
            claimable_count=None,
            stale_processing_count=None,
            exhausted_count=None,
            total_attempt_count=None,
        )
    )

    summary = (
        get_alert_delivery_operational_summary(
            db,
            organization_id=organization_id,
            now=NOW,
        )
    )

    assert summary.total_count == 0

    assert summary.pending_count == 0
    assert summary.processing_count == 0
    assert summary.retryable_count == 0
    assert summary.failed_count == 0
    assert summary.delivered_count == 0

    assert summary.claimable_count == 0
    assert (
        summary.stale_processing_count
        == 0
    )
    assert summary.exhausted_count == 0

    assert (
        summary.total_attempt_count
        == 0
    )


@pytest.mark.parametrize(
    (
        "max_attempts",
        "claim_timeout_seconds",
        "message",
    ),
    [
        (
            0,
            300,
            "Max attempts must be at least 1.",
        ),
        (
            5,
            0,
            (
                "Claim timeout seconds must "
                "be at least 1."
            ),
        ),
    ],
)
def test_summary_rejects_invalid_runtime_policy(
    max_attempts,
    claim_timeout_seconds,
    message,
):
    db = MagicMock()

    with pytest.raises(
        ValueError,
        match=message,
    ):
        get_alert_delivery_operational_summary(
            db,
            organization_id=uuid.uuid4(),
            now=NOW,
            max_attempts=max_attempts,
            claim_timeout_seconds=(
                claim_timeout_seconds
            ),
        )

    db.query.assert_not_called()