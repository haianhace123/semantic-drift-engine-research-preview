from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models.pipeline_checkpoint import PipelineCheckpoint


class PipelineCheckpointValidationError(ValueError):
    """Raised when a pipeline checkpoint input is invalid."""


@dataclass(frozen=True)
class PipelineSourceWatermark:
    """
    Stable upstream source position.

    Ordering is lexicographic by:

        source_created_at
        source_record_id

    source_created_at is the materialization-time watermark.
    source_record_id provides a deterministic tie-breaker when
    multiple upstream rows share the same timestamp.
    """

    source_created_at: datetime
    source_record_id: uuid.UUID
    knowledge_cursor: datetime


@dataclass(frozen=True)
class PipelineCheckpointResult:
    checkpoint_id: uuid.UUID
    organization_id: uuid.UUID
    pipeline_name: str
    source_created_at: datetime
    source_record_id: uuid.UUID
    knowledge_cursor: datetime
    processed_at: datetime
    metadata_json: dict[str, Any] | None
    advanced: bool


def _as_uuid(
    value,
    *,
    field_name: str,
) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value

    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PipelineCheckpointValidationError(
            f"{field_name} must be a valid UUID."
        ) from exc


def _normalize_datetime(
    value: datetime,
    *,
    field_name: str,
) -> datetime:
    if not isinstance(value, datetime):
        raise PipelineCheckpointValidationError(
            f"{field_name} must be a datetime."
        )

    if value.tzinfo is None or value.utcoffset() is None:
        raise PipelineCheckpointValidationError(
            f"{field_name} must be timezone-aware."
        )

    return value.astimezone(timezone.utc)


def _normalize_pipeline_name(
    value: str,
) -> str:
    if not isinstance(value, str):
        raise PipelineCheckpointValidationError(
            "pipeline_name must be a string."
        )

    normalized = value.strip()

    if not normalized:
        raise PipelineCheckpointValidationError(
            "pipeline_name must not be empty."
        )

    if len(normalized) > 64:
        raise PipelineCheckpointValidationError(
            "pipeline_name must not exceed 64 characters."
        )

    return normalized


def _normalize_metadata(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None

    if not isinstance(value, Mapping):
        raise PipelineCheckpointValidationError(
            "metadata_json must be a mapping or None."
        )

    return dict(value)


def _result_from_row(
    row: PipelineCheckpoint,
    *,
    advanced: bool,
) -> PipelineCheckpointResult:
    return PipelineCheckpointResult(
        checkpoint_id=row.id,
        organization_id=row.organization_id,
        pipeline_name=row.pipeline_name,
        source_created_at=row.source_created_at,
        source_record_id=row.source_record_id,
        knowledge_cursor=row.knowledge_cursor,
        processed_at=row.processed_at,
        metadata_json=(
            dict(row.metadata_json)
            if isinstance(row.metadata_json, dict)
            else row.metadata_json
        ),
        advanced=advanced,
    )


def load_pipeline_checkpoint(
    db: Session,
    *,
    organization_id,
    pipeline_name: str,
) -> PipelineCheckpoint | None:
    """
    Load one organization-scoped pipeline checkpoint.

    No mutation, flush, commit, or rollback is performed.
    """

    organization_uuid = _as_uuid(
        organization_id,
        field_name="organization_id",
    )
    normalized_pipeline_name = _normalize_pipeline_name(
        pipeline_name
    )

    return (
        db.query(PipelineCheckpoint)
        .filter(
            PipelineCheckpoint.organization_id
            == organization_uuid,
            PipelineCheckpoint.pipeline_name
            == normalized_pipeline_name,
        )
        .one_or_none()
    )


def source_watermark_is_newer(
    *,
    candidate_created_at: datetime,
    candidate_record_id,
    checkpoint: PipelineCheckpoint | None,
) -> bool:
    """
    Return True when the candidate upstream source position is
    strictly newer than the persisted checkpoint.

    Timestamp is the primary ordering key. UUID string ordering
    provides a deterministic tie-breaker for equal timestamps.
    """

    candidate_time = _normalize_datetime(
        candidate_created_at,
        field_name="candidate_created_at",
    )
    candidate_id = _as_uuid(
        candidate_record_id,
        field_name="candidate_record_id",
    )

    if checkpoint is None:
        return True

    checkpoint_time = _normalize_datetime(
        checkpoint.source_created_at,
        field_name="checkpoint.source_created_at",
    )

    if candidate_time > checkpoint_time:
        return True

    if candidate_time < checkpoint_time:
        return False

    return str(candidate_id) > str(
        checkpoint.source_record_id
    )


def persist_pipeline_checkpoint(
    db: Session,
    *,
    organization_id,
    pipeline_name: str,
    source_created_at: datetime,
    source_record_id,
    knowledge_cursor: datetime,
    metadata_json: Mapping[str, Any] | None = None,
) -> PipelineCheckpointResult:
    """
    Insert or advance one durable pipeline checkpoint.

    The checkpoint advances only when the candidate upstream
    source position is lexicographically newer by:

        (source_created_at, source_record_id)

    This protects against a stale or slower worker overwriting a
    newer checkpoint.

    knowledge_cursor and metadata_json are updated only when the
    source watermark advances.

    The caller owns commit/rollback.
    """

    organization_uuid = _as_uuid(
        organization_id,
        field_name="organization_id",
    )
    normalized_pipeline_name = _normalize_pipeline_name(
        pipeline_name
    )
    normalized_source_created_at = _normalize_datetime(
        source_created_at,
        field_name="source_created_at",
    )
    normalized_source_record_id = _as_uuid(
        source_record_id,
        field_name="source_record_id",
    )
    normalized_knowledge_cursor = _normalize_datetime(
        knowledge_cursor,
        field_name="knowledge_cursor",
    )
    normalized_metadata = _normalize_metadata(
        metadata_json
    )

    table = PipelineCheckpoint.__table__

    candidate_id = uuid.uuid4()

    insert_statement = insert(table).values(
        id=candidate_id,
        organization_id=organization_uuid,
        pipeline_name=normalized_pipeline_name,
        source_created_at=normalized_source_created_at,
        source_record_id=normalized_source_record_id,
        knowledge_cursor=normalized_knowledge_cursor,
        metadata_json=normalized_metadata,
    )

    excluded = insert_statement.excluded

    should_advance = (
        (excluded.source_created_at > table.c.source_created_at)
        | (
            (
                excluded.source_created_at
                == table.c.source_created_at
            )
            & (
                excluded.source_record_id
                > table.c.source_record_id
            )
        )
    )

    statement = (
        insert_statement
        .on_conflict_do_update(
            index_elements=[
                table.c.organization_id,
                table.c.pipeline_name,
            ],
            set_={
                "source_created_at":
                    excluded.source_created_at,
                "source_record_id":
                    excluded.source_record_id,
                "knowledge_cursor":
                    excluded.knowledge_cursor,
                "processed_at":
                    datetime.now(timezone.utc),
                "metadata_json":
                    excluded.metadata_json,
            },
            where=should_advance,
        )
        .returning(
            table.c.id,
        )
    )

    changed_id = db.execute(
        statement
    ).scalar_one_or_none()

    row = (
        db.query(PipelineCheckpoint)
        .filter(
            PipelineCheckpoint.organization_id
            == organization_uuid,
            PipelineCheckpoint.pipeline_name
            == normalized_pipeline_name,
        )
        .one_or_none()
    )

    if row is None:
        raise RuntimeError(
            "Pipeline checkpoint persistence completed but "
            "the checkpoint could not be loaded."
        )

    return _result_from_row(
        row,
        advanced=changed_id is not None,
    )