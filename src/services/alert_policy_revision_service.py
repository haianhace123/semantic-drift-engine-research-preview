from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models.organization_alert_policy_revision import (
    OrganizationAlertPolicyRevision,
)


class AlertPolicyRevisionValidationError(ValueError):
    """Raised when Alert policy revision inputs are invalid."""


class AlertPolicyRevisionConflictError(RuntimeError):
    """Raised when reconciliation state changes unexpectedly."""


@dataclass(frozen=True)
class AlertPolicyRevisionState:
    organization_id: uuid.UUID
    policy_revision: int
    reconciled_revision: int
    updated_at: datetime
    reconciled_at: datetime | None

    @property
    def reconciliation_required(self) -> bool:
        return (
            self.policy_revision
            > self.reconciled_revision
        )


def _as_uuid(
    value,
    *,
    field_name: str,
) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value

    try:
        return uuid.UUID(
            str(value)
        )
    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        raise AlertPolicyRevisionValidationError(
            f"{field_name} must be a valid UUID."
        ) from exc


def _state_from_row(
    row: OrganizationAlertPolicyRevision,
) -> AlertPolicyRevisionState:
    return AlertPolicyRevisionState(
        organization_id=row.organization_id,
        policy_revision=int(
            row.policy_revision
        ),
        reconciled_revision=int(
            row.reconciled_revision
        ),
        updated_at=row.updated_at,
        reconciled_at=row.reconciled_at,
    )


def load_alert_policy_revision(
    db: Session,
    *,
    organization_id,
    for_update: bool = False,
) -> AlertPolicyRevisionState | None:
    """
    Load one organization's Alert policy revision state.

    When for_update is True, the revision row is locked until the
    caller's transaction completes. This is the reconciliation
    serialization boundary.

    No flush, commit, or rollback is performed.
    """

    organization_uuid = _as_uuid(
        organization_id,
        field_name="organization_id",
    )

    query = (
        db.query(
            OrganizationAlertPolicyRevision
        )
        .filter(
            OrganizationAlertPolicyRevision.organization_id
            == organization_uuid
        )
    )

    if for_update:
        query = query.with_for_update()

    row = query.one_or_none()

    if row is None:
        return None

    return _state_from_row(
        row
    )


def bump_alert_policy_revision(
    db: Session,
    *,
    organization_id,
) -> AlertPolicyRevisionState:
    """
    Atomically increment one organization's Alert policy revision.

    The operation is concurrency-safe through PostgreSQL
    INSERT ... ON CONFLICT DO UPDATE.

    The caller owns commit/rollback.
    """

    organization_uuid = _as_uuid(
        organization_id,
        field_name="organization_id",
    )

    table = (
        OrganizationAlertPolicyRevision.__table__
    )

    insert_statement = insert(
        table
    ).values(
        organization_id=organization_uuid,
        policy_revision=1,
        reconciled_revision=0,
        updated_at=func.now(),
        reconciled_at=None,
    )

    statement = (
        insert_statement
        .on_conflict_do_update(
            index_elements=[
                table.c.organization_id,
            ],
            set_={
                "policy_revision":
                    table.c.policy_revision + 1,
                "updated_at":
                    func.now(),
            },
        )
        .returning(
            table.c.policy_revision,
        )
    )

    revision = db.execute(
        statement
    ).scalar_one()

    row = (
        db.query(
            OrganizationAlertPolicyRevision
        )
        .filter(
            OrganizationAlertPolicyRevision.organization_id
            == organization_uuid
        )
        .one()
    )

    if int(row.policy_revision) != int(
        revision
    ):
        raise RuntimeError(
            "Alert policy revision persistence completed "
            "with inconsistent revision state."
        )

    return _state_from_row(
        row
    )


def mark_alert_policy_reconciled(
    db: Session,
    *,
    organization_id,
    policy_revision: int,
) -> AlertPolicyRevisionState:
    """
    Mark one locked policy revision as successfully reconciled.

    The current policy_revision must still equal the revision that
    reconciliation evaluated. This prevents a stale reconciliation
    result from acknowledging newer policy state.

    The caller owns commit/rollback.
    """

    organization_uuid = _as_uuid(
        organization_id,
        field_name="organization_id",
    )

    if (
        not isinstance(policy_revision, int)
        or isinstance(policy_revision, bool)
        or policy_revision < 0
    ):
        raise AlertPolicyRevisionValidationError(
            "policy_revision must be a non-negative integer."
        )

    table = (
        OrganizationAlertPolicyRevision.__table__
    )

    statement = (
        update(table)
        .where(
            table.c.organization_id
            == organization_uuid,
            table.c.policy_revision
            == policy_revision,
            table.c.reconciled_revision
            < policy_revision,
        )
        .values(
            reconciled_revision=policy_revision,
            reconciled_at=func.now(),
        )
        .returning(
            table.c.organization_id,
        )
    )

    changed_organization_id = (
        db.execute(
            statement
        ).scalar_one_or_none()
    )

    row = (
        db.query(
            OrganizationAlertPolicyRevision
        )
        .filter(
            OrganizationAlertPolicyRevision.organization_id
            == organization_uuid
        )
        .one_or_none()
    )

    if row is None:
        raise AlertPolicyRevisionConflictError(
            "Alert policy revision does not exist."
        )

    if changed_organization_id is None:
        if (
            int(row.policy_revision)
            != policy_revision
        ):
            raise AlertPolicyRevisionConflictError(
                "Alert policy revision changed before "
                "reconciliation could be acknowledged."
            )

        if (
            int(row.reconciled_revision)
            == policy_revision
        ):
            return _state_from_row(
                row
            )

        raise AlertPolicyRevisionConflictError(
            "Alert policy reconciliation state could not "
            "be advanced."
        )

    return _state_from_row(
        row
    )
