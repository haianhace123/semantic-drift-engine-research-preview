from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from src.models.lineage_invalidation_execution_attempt import (
    LineageInvalidationExecutionAttemptRecord,
)
from src.models.lineage_invalidation_execution_intent import (
    LineageInvalidationExecutionIntentRecord,
)
from src.models.lineage_invalidation_execution_result import (
    LineageInvalidationExecutionResultRecord,
)


@dataclass(frozen=True, slots=True)
class LineageInvalidationExecutionCandidate:
    organization_id: uuid.UUID
    intent_id: uuid.UUID
    authorized_at: datetime


DEFAULT_EXECUTION_DISCOVERY_LIMIT = 32
EXECUTION_RETRY_COOLDOWN = timedelta(seconds=60)


def discover_lineage_invalidation_execution_candidates(
    db: Session,
    *,
    organization_id: uuid.UUID | None = None,
    limit: int = DEFAULT_EXECUTION_DISCOVERY_LIMIT,
    after_authorized_at: datetime | None = None,
    after_intent_id: uuid.UUID | None = None,
) -> tuple[LineageInvalidationExecutionCandidate, ...]:
    """
    Discover an ordered bounded set of authorized execution intents that may
    be claimable.

    Discovery does not grant execution authority. The caller must still use
    claim_lineage_invalidation_execution() and commit that claim before any
    external side effect.

    A candidate is an intent that has never been attempted, or whose latest
    attempt has expired without a terminal result.
    """
    if limit < 1:
        raise ValueError("Execution discovery limit must be at least 1.")

    if (after_authorized_at is None) != (after_intent_id is None):
        raise ValueError(
            "Execution discovery cursor requires both authorized_at "
            "and intent_id."
        )

    effective_now = db.execute(
        select(func.clock_timestamp())
    ).scalar_one()

    latest_attempt_number = (
        db.query(
            LineageInvalidationExecutionAttemptRecord.organization_id.label(
                "organization_id"
            ),
            LineageInvalidationExecutionAttemptRecord.intent_id.label(
                "intent_id"
            ),
            func.max(
                LineageInvalidationExecutionAttemptRecord.attempt_number
            ).label("attempt_number"),
        )
        .group_by(
            LineageInvalidationExecutionAttemptRecord.organization_id,
            LineageInvalidationExecutionAttemptRecord.intent_id,
        )
        .subquery()
    )

    latest_attempt = aliased(
        LineageInvalidationExecutionAttemptRecord
    )

    query = (
        db.query(LineageInvalidationExecutionIntentRecord)
        .outerjoin(
            latest_attempt_number,
            (
                latest_attempt_number.c.organization_id
                == LineageInvalidationExecutionIntentRecord.organization_id
            )
            & (
                latest_attempt_number.c.intent_id
                == LineageInvalidationExecutionIntentRecord.id
            ),
        )
        .outerjoin(
            latest_attempt,
            (
                latest_attempt.organization_id
                == latest_attempt_number.c.organization_id
            )
            & (
                latest_attempt.intent_id
                == latest_attempt_number.c.intent_id
            )
            & (
                latest_attempt.attempt_number
                == latest_attempt_number.c.attempt_number
            ),
        )
        .outerjoin(
            LineageInvalidationExecutionResultRecord,
            (
                LineageInvalidationExecutionResultRecord.organization_id
                == latest_attempt.organization_id
            )
            & (
                LineageInvalidationExecutionResultRecord.attempt_id
                == latest_attempt.id
            ),
        )
        .filter(
            or_(
                latest_attempt.id.is_(None),
                (
                    LineageInvalidationExecutionResultRecord.id.is_(None)
                    & (
                        latest_attempt.lease_expires_at
                        + EXECUTION_RETRY_COOLDOWN
                        <= effective_now
                    )
                ),
            )
        )
    )

    if organization_id is not None:
        query = query.filter(
            LineageInvalidationExecutionIntentRecord.organization_id
            == organization_id
        )

    if after_authorized_at is not None and after_intent_id is not None:
        query = query.filter(
            or_(
                LineageInvalidationExecutionIntentRecord.authorized_at
                > after_authorized_at,
                (
                    LineageInvalidationExecutionIntentRecord.authorized_at
                    == after_authorized_at
                )
                & (
                    LineageInvalidationExecutionIntentRecord.id
                    > after_intent_id
                ),
            )
        )

    intents = (
        query.order_by(
            LineageInvalidationExecutionIntentRecord.authorized_at.asc(),
            LineageInvalidationExecutionIntentRecord.id.asc(),
        )
        .limit(limit)
        .all()
    )

    return tuple(
        LineageInvalidationExecutionCandidate(
            organization_id=intent.organization_id,
            intent_id=intent.id,
            authorized_at=intent.authorized_at,
        )
        for intent in intents
    )


def discover_lineage_invalidation_execution_candidate(
    db: Session,
    *,
    organization_id: uuid.UUID | None = None,
) -> LineageInvalidationExecutionCandidate | None:
    candidates = discover_lineage_invalidation_execution_candidates(
        db,
        organization_id=organization_id,
        limit=1,
    )

    if not candidates:
        return None

    return candidates[0]
