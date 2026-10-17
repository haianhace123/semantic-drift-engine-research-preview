from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.lineage_invalidation_execution_attempt import (
    LineageInvalidationExecutionAttemptRecord,
)
from src.models.lineage_invalidation_execution_intent import (
    LineageInvalidationExecutionIntentRecord,
)
from src.models.lineage_invalidation_execution_result import (
    LineageInvalidationExecutionResultRecord,
)
from src.domain.lineage_execution_target import (
    LineageExecutionTargetIdentity,
)
from src.services.lineage_authority_lock_service import (
    acquire_lineage_policy_authority_lock,
    acquire_lineage_target_authority_lock,
)
from src.services.lineage_invalidation_eligibility_service import (
    LineageInvalidationEligibilityReason,
    evaluate_lineage_invalidation_eligibility,
)
from src.services.lineage_invalidation_projection_service import (
    load_lineage_invalidation_decision_by_record_id,
)
from src.services.lineage_execution_target_binding_service import (
    load_current_lineage_execution_target_binding,
)
from src.services.lineage_pgvector_invalidation_service import (
    LANGCHAIN_PGVECTOR_DOCUMENT_TARGET_KIND,
    LANGCHAIN_PGVECTOR_DOCUMENT_REVISION_TARGET_KIND,
    LANGCHAIN_PGVECTOR_CAUSAL_CHUNK_TARGET_KIND,
    PGVECTOR_PROVIDER,
)
from src.services.lineage_invalidation_execution_target_guard import (
    LineageInvalidationExecutionTargetCausalMismatchError,
    resolve_causal_chunk_target_configuration,
)
from src.services.lineage_invalidation_execution_discovery_service import (
    EXECUTION_RETRY_COOLDOWN,
)


DEFAULT_EXECUTION_CLAIM_LEASE_SECONDS = 300


@dataclass(frozen=True, slots=True)
class LineageInvalidationExecutionClaimResult:
    record: LineageInvalidationExecutionAttemptRecord


class LineageInvalidationExecutionClaimError(RuntimeError):
    """Base error for execution claim failures."""


class LineageInvalidationExecutionIntentNotFoundError(
    LineageInvalidationExecutionClaimError
):
    """Raised when the requested execution intent is unavailable."""


class LineageInvalidationExecutionClaimNotEligibleError(
    LineageInvalidationExecutionClaimError
):
    def __init__(
        self,
        *,
        reason_code: LineageInvalidationEligibilityReason,
    ) -> None:
        self.reason_code = reason_code
        super().__init__(
            "Lineage invalidation execution intent is not currently "
            f"eligible for claim: {reason_code}."
        )


class LineageInvalidationExecutionClaimUnavailableError(
    LineageInvalidationExecutionClaimError
):
    def __init__(
        self,
        *,
        current_attempt_id: uuid.UUID,
        lease_expires_at: datetime,
    ) -> None:
        self.current_attempt_id = current_attempt_id
        self.lease_expires_at = lease_expires_at
        super().__init__(
            "Lineage invalidation execution intent already has a "
            "current unexpired execution attempt."
        )


class LineageInvalidationExecutionRetryDeferredError(
    LineageInvalidationExecutionClaimError
):
    def __init__(
        self,
        *,
        current_attempt_id: uuid.UUID,
        retry_after: datetime,
    ) -> None:
        self.current_attempt_id = current_attempt_id
        self.retry_after = retry_after
        super().__init__(
            "Execution retry is deferred by the retry cooldown."
        )


class LineageInvalidationExecutionClaimTerminalError(
    LineageInvalidationExecutionClaimError
):
    def __init__(
        self,
        *,
        current_attempt_id: uuid.UUID,
        outcome: str,
    ) -> None:
        self.current_attempt_id = current_attempt_id
        self.outcome = outcome
        super().__init__(
            "Lineage invalidation execution intent already has a "
            "terminal execution result."
        )


class LineageInvalidationExecutionTargetBindingNotFoundError(
    LineageInvalidationExecutionClaimError
):
    """Raised when the claimed logical target has no execution binding."""


class LineageInvalidationExecutionUnsafeTargetError(
    LineageInvalidationExecutionClaimError
):
    """Raised when an execution target cannot safely be retried."""


class LineageInvalidationExecutionClaimConfigurationError(
    LineageInvalidationExecutionClaimError
):
    """Raised when execution claim parameters are invalid."""


def claim_lineage_invalidation_execution(
    db: Session,
    *,
    organization_id: uuid.UUID,
    intent_id: uuid.UUID,
    lease_seconds: int = DEFAULT_EXECUTION_CLAIM_LEASE_SECONDS,
) -> LineageInvalidationExecutionClaimResult:
    """
    Atomically claim one exact authorized invalidation execution intent.

    The execution intent row serializes attempt-number allocation for this
    intent. Policy and target advisory locks then serialize claim-time
    eligibility with the corresponding authority writers.

    The caller owns commit / rollback and must commit the returned claim
    before performing any external side effect.
    """
    if lease_seconds < 1:
        raise LineageInvalidationExecutionClaimConfigurationError(
            "Execution claim lease seconds must be at least 1."
        )

    intent = (
        db.query(LineageInvalidationExecutionIntentRecord)
        .filter(
            LineageInvalidationExecutionIntentRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionIntentRecord.id == intent_id,
        )
        .with_for_update()
        .one_or_none()
    )

    if intent is None:
        raise LineageInvalidationExecutionIntentNotFoundError(
            "Lineage invalidation execution intent was not found."
        )

    persisted = load_lineage_invalidation_decision_by_record_id(
        db,
        organization_id=organization_id,
        record_id=intent.decision_id,
    )

    if persisted is None:
        raise LineageInvalidationExecutionIntentNotFoundError(
            "Execution intent decision was not found."
        )

    decision = persisted.decision

    acquire_lineage_policy_authority_lock(
        db,
        organization_id=organization_id,
        policy_external_id=decision.policy.policy_external_id,
    )
    acquire_lineage_target_authority_lock(
        db,
        organization_id=organization_id,
        source_external_id=decision.target.source_external_id,
        asset_external_id=decision.target.asset_external_id,
    )

    persisted = load_lineage_invalidation_decision_by_record_id(
        db,
        organization_id=organization_id,
        record_id=intent.decision_id,
    )

    if persisted is None:
        raise LineageInvalidationExecutionIntentNotFoundError(
            "Execution intent decision was not found."
        )

    decision = persisted.decision

    eligibility = evaluate_lineage_invalidation_eligibility(
        db,
        organization_id=organization_id,
        decision=decision,
    )

    if not eligibility.eligible:
        raise LineageInvalidationExecutionClaimNotEligibleError(
            reason_code=eligibility.reason_code,
        )

    target_identity = LineageExecutionTargetIdentity(
        source_external_id=decision.target.source_external_id,
        asset_external_id=decision.target.asset_external_id,
    )
    target_binding = load_current_lineage_execution_target_binding(
        db,
        organization_id=organization_id,
        identity=target_identity,
    )

    if target_binding is None:
        raise LineageInvalidationExecutionTargetBindingNotFoundError(
            "Execution target binding was not found for the claimed target."
        )

    if (
        target_binding.provider == PGVECTOR_PROVIDER
        and target_binding.target_kind in (
            LANGCHAIN_PGVECTOR_DOCUMENT_TARGET_KIND,
            LANGCHAIN_PGVECTOR_DOCUMENT_REVISION_TARGET_KIND,
        )
    ):
        raise LineageInvalidationExecutionUnsafeTargetError(
            "Legacy PGVector document bindings cannot establish causal "
            "physical identity. Publish a causal-chunk binding revision."
        )

    if target_binding.target_kind == LANGCHAIN_PGVECTOR_CAUSAL_CHUNK_TARGET_KIND:
        try:
            resolve_causal_chunk_target_configuration(
                db,
                organization_id=organization_id,
                decision=decision,
                provider=target_binding.provider,
                target_kind=target_binding.target_kind,
                configuration=target_binding.configuration,
            )
        except LineageInvalidationExecutionTargetCausalMismatchError as exc:
            raise LineageInvalidationExecutionUnsafeTargetError(str(exc)) from exc

    effective_now = db.execute(
        select(func.clock_timestamp())
    ).scalar_one()

    latest_attempt = (
        db.query(LineageInvalidationExecutionAttemptRecord)
        .filter(
            LineageInvalidationExecutionAttemptRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionAttemptRecord.intent_id == intent.id,
        )
        .order_by(
            LineageInvalidationExecutionAttemptRecord.attempt_number.desc(),
        )
        .first()
    )

    if latest_attempt is not None:
        terminal_result = (
            db.query(LineageInvalidationExecutionResultRecord)
            .filter(
                LineageInvalidationExecutionResultRecord.organization_id
                == organization_id,
                LineageInvalidationExecutionResultRecord.attempt_id
                == latest_attempt.id,
            )
            .one_or_none()
        )

        if terminal_result is not None:
            raise LineageInvalidationExecutionClaimTerminalError(
                current_attempt_id=latest_attempt.id,
                outcome=terminal_result.outcome,
            )

        if latest_attempt.lease_expires_at > effective_now:
            raise LineageInvalidationExecutionClaimUnavailableError(
                current_attempt_id=latest_attempt.id,
                lease_expires_at=latest_attempt.lease_expires_at,
            )
        retry_after = (
            latest_attempt.lease_expires_at
            + EXECUTION_RETRY_COOLDOWN
        )

        if effective_now < retry_after:
            raise LineageInvalidationExecutionRetryDeferredError(
                current_attempt_id=latest_attempt.id,
                retry_after=retry_after,
            )
    attempt_number = (
        1
        if latest_attempt is None
        else latest_attempt.attempt_number + 1
    )

    record = LineageInvalidationExecutionAttemptRecord(
        organization_id=organization_id,
        intent_id=intent.id,
        attempt_number=attempt_number,
        binding_revision=target_binding.binding_revision,
        claimed_at=effective_now,
        lease_expires_at=(
            effective_now + timedelta(seconds=lease_seconds)
        ),
    )
    db.add(record)
    db.flush()

    return LineageInvalidationExecutionClaimResult(
        record=record,
    )