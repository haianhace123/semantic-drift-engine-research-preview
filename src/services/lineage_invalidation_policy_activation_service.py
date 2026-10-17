from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.domain.lineage_invalidation import (
    LineageInvalidationPolicyIdentity,
)
from src.models.lineage_invalidation_policy_activation import (
    LineageInvalidationPolicyActivationRecord,
)
from src.services.lineage_authority_lock_service import (
    acquire_lineage_policy_authority_lock,
)


LineageInvalidationPolicyActivationOutcome = Literal[
    "activated",
    "replayed",
]


class LineageInvalidationPolicyStaleActivationError(RuntimeError):
    """Raised when an older policy revision attempts to replace a newer one."""


@dataclass(frozen=True)
class LineageInvalidationPolicyActivationState:
    organization_id: uuid.UUID
    policy: LineageInvalidationPolicyIdentity
    activated_at: datetime


@dataclass(frozen=True)
class LineageInvalidationPolicyActivationResult:
    state: LineageInvalidationPolicyActivationState
    outcome: LineageInvalidationPolicyActivationOutcome


def activate_lineage_invalidation_policy(
    db: Session,
    *,
    organization_id: uuid.UUID,
    policy: LineageInvalidationPolicyIdentity,
) -> LineageInvalidationPolicyActivationResult:
    acquire_lineage_policy_authority_lock(
        db,
        organization_id=organization_id,
        policy_external_id=policy.policy_external_id,
    )

    table = LineageInvalidationPolicyActivationRecord.__table__

    insert_statement = insert(table).values(
        organization_id=organization_id,
        policy_external_id=policy.policy_external_id,
        active_revision=policy.revision,
    )

    statement = (
        insert_statement
        .on_conflict_do_update(
            index_elements=[
                table.c.organization_id,
                table.c.policy_external_id,
            ],
            set_={
                "active_revision": policy.revision,
                "activated_at": func.now(),
            },
            where=(
                table.c.active_revision
                < policy.revision
            ),
        )
        .returning(
            table.c.organization_id,
            table.c.policy_external_id,
            table.c.active_revision,
            table.c.activated_at,
        )
    )

    activated = db.execute(statement).one_or_none()

    if activated is not None:
        return LineageInvalidationPolicyActivationResult(
            state=_state_from_values(
                organization_id=activated.organization_id,
                policy_external_id=activated.policy_external_id,
                active_revision=activated.active_revision,
                activated_at=activated.activated_at,
            ),
            outcome="activated",
        )

    current = _load_activation(
        db,
        organization_id=organization_id,
        policy_external_id=policy.policy_external_id,
    )

    if current is None:
        raise RuntimeError(
            "Lineage invalidation policy activation "
            "did not produce durable authority state."
        )

    if current.active_revision == policy.revision:
        return LineageInvalidationPolicyActivationResult(
            state=_state_from_record(current),
            outcome="replayed",
        )

    if current.active_revision > policy.revision:
        raise LineageInvalidationPolicyStaleActivationError(
            "Cannot activate lineage invalidation policy revision "
            f"{policy.revision}; revision "
            f"{current.active_revision} is already active."
        )

    raise RuntimeError(
        "Lineage invalidation policy activation state "
        "violated monotonic revision semantics."
    )


def load_active_lineage_invalidation_policy(
    db: Session,
    *,
    organization_id: uuid.UUID,
    policy_external_id: str,
) -> LineageInvalidationPolicyActivationState | None:
    normalized_policy_external_id = policy_external_id.strip()

    if not normalized_policy_external_id:
        raise ValueError(
            "policy_external_id must not be empty."
        )

    LineageInvalidationPolicyIdentity(
        policy_external_id=normalized_policy_external_id,
        revision=0,
    )

    current = _load_activation(
        db,
        organization_id=organization_id,
        policy_external_id=normalized_policy_external_id,
    )

    if current is None:
        return None

    return _state_from_record(current)


def _load_activation(
    db: Session,
    *,
    organization_id: uuid.UUID,
    policy_external_id: str,
) -> LineageInvalidationPolicyActivationRecord | None:
    return (
        db.query(LineageInvalidationPolicyActivationRecord)
        .filter(
            LineageInvalidationPolicyActivationRecord.organization_id
            == organization_id,
            LineageInvalidationPolicyActivationRecord.policy_external_id
            == policy_external_id,
        )
        .one_or_none()
    )


def _state_from_record(
    record: LineageInvalidationPolicyActivationRecord,
) -> LineageInvalidationPolicyActivationState:
    return _state_from_values(
        organization_id=record.organization_id,
        policy_external_id=record.policy_external_id,
        active_revision=record.active_revision,
        activated_at=record.activated_at,
    )


def _state_from_values(
    *,
    organization_id: uuid.UUID,
    policy_external_id: str,
    active_revision: int,
    activated_at: datetime,
) -> LineageInvalidationPolicyActivationState:
    return LineageInvalidationPolicyActivationState(
        organization_id=organization_id,
        policy=LineageInvalidationPolicyIdentity(
            policy_external_id=policy_external_id,
            revision=int(active_revision),
        ),
        activated_at=activated_at,
    )