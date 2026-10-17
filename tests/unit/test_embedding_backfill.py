import uuid

import pytest

from scripts.embedding_backfill import (
    MAX_DRY_RUN_LIMIT,
    MAX_EXECUTE_BATCH,
    PROVIDER_PROBE_TEXT,
    _parse_snapshot_id,
    probe_embedding_provider,
    validate_backfill_request,
)
from src.services.embedding_service import (
    EmbeddingProviderUnavailableError,
)

class HealthyEmbeddingService:
    def __init__(self) -> None:
        self.probe_texts: list[str] = []

    def generate_embedding(
        self,
        text: str,
    ) -> list[float]:
        self.probe_texts.append(
            text
        )

        return [
            0.0
        ]


class UnavailableEmbeddingService:
    def __init__(self) -> None:
        self.probe_texts: list[str] = []

    def generate_embedding(
        self,
        text: str,
    ) -> list[float]:
        self.probe_texts.append(
            text
        )

        raise EmbeddingProviderUnavailableError(
            "Provider quota exhausted."
        )

def test_parse_snapshot_id_accepts_uuid():
    value = uuid.uuid4()

    assert (
        _parse_snapshot_id(
            str(
                value
            )
        )
        == value
    )


def test_parse_snapshot_id_rejects_invalid_uuid():
    with pytest.raises(
        Exception,
        match="Invalid snapshot UUID",
    ):
        _parse_snapshot_id(
            "not-a-uuid"
        )


def test_backfill_request_rejects_zero_limit():
    with pytest.raises(
        ValueError,
        match="limit must be at least 1",
    ):
        validate_backfill_request(
            limit=0,
            max_attempts=3,
            execute=False,
            snapshot_ids=None,
        )


def test_backfill_request_rejects_zero_max_attempts():
    with pytest.raises(
        ValueError,
        match=(
            "max_attempts must be "
            "at least 1"
        ),
    ):
        validate_backfill_request(
            limit=1,
            max_attempts=0,
            execute=False,
            snapshot_ids=None,
        )


def test_dry_run_rejects_unbounded_limit():
    with pytest.raises(
        ValueError,
        match=(
            "limit cannot exceed "
            f"{MAX_DRY_RUN_LIMIT}"
        ),
    ):
        validate_backfill_request(
            limit=(
                MAX_DRY_RUN_LIMIT
                + 1
            ),
            max_attempts=3,
            execute=False,
            snapshot_ids=None,
        )


def test_execute_rejects_large_queue_batch():
    with pytest.raises(
        ValueError,
        match=(
            "execute mode cannot "
            "process more than "
            f"{MAX_EXECUTE_BATCH} "
            "queue snapshots"
        ),
    ):
        validate_backfill_request(
            limit=(
                MAX_EXECUTE_BATCH
                + 1
            ),
            max_attempts=3,
            execute=True,
            snapshot_ids=None,
        )


def test_execute_rejects_too_many_explicit_snapshot_ids():
    snapshot_ids = [
        uuid.uuid4()
        for _ in range(
            MAX_EXECUTE_BATCH
            + 1
        )
    ]

    with pytest.raises(
        ValueError,
        match=(
            "execute mode cannot "
            "process more than "
            f"{MAX_EXECUTE_BATCH} "
            "explicit snapshot IDs"
        ),
    ):
        validate_backfill_request(
            limit=5,
            max_attempts=3,
            execute=True,
            snapshot_ids=(
                snapshot_ids
            ),
        )


def test_execute_allows_small_queue_batch():
    validate_backfill_request(
        limit=1,
        max_attempts=3,
        execute=True,
        snapshot_ids=None,
    )


def test_execute_allows_small_explicit_snapshot_batch():
    validate_backfill_request(
        limit=5,
        max_attempts=3,
        execute=True,
        snapshot_ids=[
            uuid.uuid4()
        ],
    )

def test_provider_probe_passes_without_snapshot_mutation():
    service = (
        HealthyEmbeddingService()
    )

    result = probe_embedding_provider(
        service
    )

    assert result.ready is True
    assert result.error is None

    assert service.probe_texts == [
        PROVIDER_PROBE_TEXT
    ]


def test_provider_probe_reports_provider_unavailable():
    service = (
        UnavailableEmbeddingService()
    )

    result = probe_embedding_provider(
        service
    )

    assert result.ready is False

    assert result.error == (
        "Provider quota exhausted."
    )

    assert service.probe_texts == [
        PROVIDER_PROBE_TEXT
    ]