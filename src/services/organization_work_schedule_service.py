from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models.organization_work_schedule import (
    ORGANIZATION_WORK_KINDS,
    OrganizationWorkSchedule,
)


class OrganizationWorkScheduleValidationError(ValueError):
    pass


def _as_uuid(value, *, field_name: str) -> uuid.UUID:
    try:
        return (
            value
            if isinstance(value, uuid.UUID)
            else uuid.UUID(str(value))
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise OrganizationWorkScheduleValidationError(
            f"{field_name} must be a valid UUID."
        ) from exc


def _normalize_work_kind(value: str) -> str:
    if not isinstance(value, str):
        raise OrganizationWorkScheduleValidationError(
            "work_kind must be a string."
        )

    normalized = value.strip()

    if normalized not in ORGANIZATION_WORK_KINDS:
        raise OrganizationWorkScheduleValidationError(
            f"Unsupported work_kind: {normalized!r}."
        )

    return normalized


def _normalize_retry_seconds(value: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
    ):
        raise OrganizationWorkScheduleValidationError(
            "retry_seconds must be an integer of at least 1."
        )

    return value


def schedule_organization_work_retry(
    db: Session,
    *,
    organization_id,
    work_kind: str,
    retry_seconds: int,
    now: datetime | None = None,
) -> datetime:
    """
    Atomically schedule the next eligible retry for organization work.

    The caller owns commit/rollback.
    """

    organization_uuid = _as_uuid(
        organization_id,
        field_name="organization_id",
    )
    normalized_work_kind = _normalize_work_kind(
        work_kind
    )
    normalized_retry_seconds = _normalize_retry_seconds(
        retry_seconds
    )

    current_time = now or datetime.now(timezone.utc)

    if current_time.tzinfo is None:
        raise OrganizationWorkScheduleValidationError(
            "now must be timezone-aware."
        )

    retry_after = (
        current_time
        + timedelta(
            seconds=normalized_retry_seconds
        )
    )

    table = OrganizationWorkSchedule.__table__

    statement = insert(table).values(
        organization_id=organization_uuid,
        work_kind=normalized_work_kind,
        retry_after=retry_after,
        updated_at=current_time,
    )

    statement = statement.on_conflict_do_update(
        index_elements=[
            table.c.organization_id,
            table.c.work_kind,
        ],
        set_={
            "retry_after": retry_after,
            "updated_at": current_time,
        },
    )

    db.execute(statement)

    return retry_after


def clear_organization_work_schedule(
    db: Session,
    *,
    organization_id,
    work_kind: str,
) -> None:
    """
    Remove retry scheduling state after successful completion.

    The caller owns commit/rollback.
    """

    organization_uuid = _as_uuid(
        organization_id,
        field_name="organization_id",
    )
    normalized_work_kind = _normalize_work_kind(
        work_kind
    )

    (
        db.query(OrganizationWorkSchedule)
        .filter(
            OrganizationWorkSchedule.organization_id
            == organization_uuid,
            OrganizationWorkSchedule.work_kind
            == normalized_work_kind,
        )
        .delete(
            synchronize_session=False
        )
    )
