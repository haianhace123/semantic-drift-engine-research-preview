"""
Organization-scoped transactional serialization for intelligence execution.

The organizations row is the durable serialization boundary for all derived
intelligence mutations belonging to one tenant.

The acquired row lock is owned by the caller's current database transaction
and is released only when that transaction commits or rolls back.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from src.models.organization import Organization


class OrganizationExecutionLockError(
    RuntimeError
):
    """Raised when an organization execution boundary cannot be acquired."""


def acquire_organization_execution_lock(
    db: Session,
    *,
    organization_id: uuid.UUID,
) -> None:
    """
    Serialize derived intelligence execution for one organization.

    This function intentionally does not commit or roll back. The caller owns
    the surrounding transaction and therefore owns the lifetime of the lock.
    """
    organization_exists = (
        db.query(
            Organization.id
        )
        .filter(
            Organization.id
            == organization_id
        )
        .with_for_update()
        .one_or_none()
    )

    if organization_exists is None:
        raise OrganizationExecutionLockError(
            "Organization does not exist for intelligence execution: "
            f"{organization_id}."
        )