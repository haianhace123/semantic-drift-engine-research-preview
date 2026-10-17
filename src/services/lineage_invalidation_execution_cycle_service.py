from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import uuid

from src.core.database import SessionLocal
from src.services.lineage_downstream_connection_service import (
    create_lineage_downstream_session,
    resolve_lineage_downstream_connection,
)
from src.services.lineage_invalidation_execution_claim_service import (
    LineageInvalidationExecutionClaimError,
    LineageInvalidationExecutionClaimNotEligibleError,
    LineageInvalidationExecutionClaimTerminalError,
    LineageInvalidationExecutionClaimUnavailableError,
    LineageInvalidationExecutionTargetBindingNotFoundError,
    claim_lineage_invalidation_execution,
)
from src.services.lineage_invalidation_execution_discovery_service import (
    DEFAULT_EXECUTION_DISCOVERY_LIMIT,
    LineageInvalidationExecutionCandidate,
    discover_lineage_invalidation_execution_candidates,
)
from src.services.lineage_invalidation_execution_result_service import (
    persist_lineage_invalidation_execution_result,
)
from src.services.lineage_invalidation_executor_service import (
    resolve_lineage_invalidation_execution_plan,
)
from src.services.lineage_pgvector_invalidation_service import (
    LANGCHAIN_PGVECTOR_CAUSAL_CHUNK_TARGET_KIND,
    LineagePgVectorInvalidationError,
    confirm_lineage_pgvector_causal_chunk_absent,
    invalidate_lineage_pgvector_causal_chunk,
)
from src.services.lineage_invalidation_execution_claim_service import (
    LineageInvalidationExecutionUnsafeTargetError,
    LineageInvalidationExecutionRetryDeferredError,
)


class LineageInvalidationExecutionCycleStatus(str, Enum):
    IDLE = "idle"
    SKIPPED = "skipped"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class LineageInvalidationExecutionCycleError(RuntimeError):
    """Execution cycle invariant violation."""


@dataclass(frozen=True, slots=True)
class LineageInvalidationExecutionCycleResult:
    status: LineageInvalidationExecutionCycleStatus
    candidate: LineageInvalidationExecutionCandidate | None = None
    attempt_id: uuid.UUID | None = None
    reason_code: str | None = None


def _discover_candidates(
    *,
    organization_id: uuid.UUID | None = None,
    after_authorized_at: datetime | None = None,
    after_intent_id: uuid.UUID | None = None,
) -> tuple[LineageInvalidationExecutionCandidate, ...]:
    db = SessionLocal()

    try:
        candidates = discover_lineage_invalidation_execution_candidates(
            db,
            organization_id=organization_id,
            limit=DEFAULT_EXECUTION_DISCOVERY_LIMIT,
            after_authorized_at=after_authorized_at,
            after_intent_id=after_intent_id,
        )
        db.rollback()
        return candidates
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _claim_candidate(
    candidate: LineageInvalidationExecutionCandidate,
) -> uuid.UUID:
    db = SessionLocal()

    try:
        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=candidate.organization_id,
            intent_id=candidate.intent_id,
        )
        attempt_id = claim.record.id
        db.commit()
        return attempt_id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _resolve_plan(
    *,
    organization_id: uuid.UUID,
    attempt_id: uuid.UUID,
):
    db = SessionLocal()

    try:
        plan = resolve_lineage_invalidation_execution_plan(
            db,
            organization_id=organization_id,
            attempt_id=attempt_id,
        )
        db.rollback()
        return plan
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _publish_result(
    *,
    organization_id: uuid.UUID,
    attempt_id: uuid.UUID,
    outcome: str,
) -> None:
    db = SessionLocal()

    try:
        persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization_id,
            attempt_id=attempt_id,
            outcome=outcome,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_lineage_invalidation_execution_cycle(
    *,
    organization_id: uuid.UUID | None = None,
) -> LineageInvalidationExecutionCycleResult:
    """
    Discover and execute at most one authorized invalidation intent.

    The durable execution claim is committed before any downstream side
    effect. Execution planning uses a separate authority transaction.
    Downstream invalidation commits independently. Terminal result
    publication uses a fresh authority transaction.

    This provides at-least-once execution with an idempotent downstream
    invalidation, not exactly-once execution.
    """
    after_authorized_at = None
    after_intent_id = None
    discovered_any = False
    last_skipped_candidate = None
    last_skip_reason = None
    candidate = None
    attempt_id = None

    while True:
        candidates = _discover_candidates(
            organization_id=organization_id,
            after_authorized_at=after_authorized_at,
            after_intent_id=after_intent_id,
        )

        if not candidates:
            break

        discovered_any = True

        for discovered_candidate in candidates:
            try:
                claimed_attempt_id = _claim_candidate(
                    discovered_candidate
                )
            except LineageInvalidationExecutionClaimNotEligibleError as exc:
                last_skipped_candidate = discovered_candidate
                last_skip_reason = str(exc.reason_code)
                continue
            except LineageInvalidationExecutionTargetBindingNotFoundError:
                last_skipped_candidate = discovered_candidate
                last_skip_reason = "target_binding_not_found"
                continue
            except LineageInvalidationExecutionUnsafeTargetError:
                last_skipped_candidate = discovered_candidate
                last_skip_reason = "unsafe_execution_target"
                continue
            except LineageInvalidationExecutionClaimUnavailableError:
                last_skipped_candidate = discovered_candidate
                last_skip_reason = "claim_unavailable"
                continue
            except LineageInvalidationExecutionRetryDeferredError:
                last_skipped_candidate = discovered_candidate
                last_skip_reason = "retry_cooldown"
                continue
            except LineageInvalidationExecutionClaimTerminalError:
                last_skipped_candidate = discovered_candidate
                last_skip_reason = "claim_terminal"
                continue
            except LineageInvalidationExecutionClaimError:
                raise

            candidate = discovered_candidate
            attempt_id = claimed_attempt_id
            break

        if candidate is not None:
            break

        last_candidate = candidates[-1]
        after_authorized_at = last_candidate.authorized_at
        after_intent_id = last_candidate.intent_id

        if len(candidates) < DEFAULT_EXECUTION_DISCOVERY_LIMIT:
            break

    if candidate is None or attempt_id is None:
        if not discovered_any:
            return LineageInvalidationExecutionCycleResult(
                status=LineageInvalidationExecutionCycleStatus.IDLE,
            )

        return LineageInvalidationExecutionCycleResult(
            status=LineageInvalidationExecutionCycleStatus.SKIPPED,
            candidate=last_skipped_candidate,
            reason_code=last_skip_reason,
        )

    plan = _resolve_plan(
        organization_id=candidate.organization_id,
        attempt_id=attempt_id,
    )

    connection = resolve_lineage_downstream_connection(
        connection_ref=plan.connection_ref,
    )
    downstream_db, downstream_engine = (
        create_lineage_downstream_session(connection)
    )

    try:
        try:
            if plan.target_kind == LANGCHAIN_PGVECTOR_CAUSAL_CHUNK_TARGET_KIND:
                invalidate_lineage_pgvector_causal_chunk(
                    downstream_db,
                    configuration=plan.target_configuration,
                )
            else:
                raise LineageInvalidationExecutionCycleError(
                    "Resolved execution plan has an unsupported target kind."
                )
        except Exception:
            downstream_db.rollback()

            _publish_result(
                organization_id=candidate.organization_id,
                attempt_id=attempt_id,
                outcome="failed",
            )

            return LineageInvalidationExecutionCycleResult(
                status=LineageInvalidationExecutionCycleStatus.FAILED,
                candidate=candidate,
                attempt_id=attempt_id,
                reason_code="downstream_execution_failed",
            )

        try:
            downstream_db.commit()
        except Exception:
            downstream_db.rollback()
            raise

        # Read back after commit in a fresh downstream transaction. A
        # successful DELETE statement is not sufficient physical evidence.
        # SQL/connection errors here remain retryable (lease reclaim); only
        # a definitive physical/provenance mismatch is terminalized.
        try:
            confirm_lineage_pgvector_causal_chunk_absent(
                downstream_db,
                configuration=plan.target_configuration,
            )
        except LineagePgVectorInvalidationError:
            downstream_db.rollback()
            _publish_result(
                organization_id=candidate.organization_id,
                attempt_id=attempt_id,
                outcome="failed",
            )
            return LineageInvalidationExecutionCycleResult(
                status=LineageInvalidationExecutionCycleStatus.FAILED,
                candidate=candidate,
                attempt_id=attempt_id,
                reason_code="downstream_physical_confirmation_failed",
            )
        finally:
            downstream_db.rollback()
    finally:
        downstream_db.close()
        downstream_engine.dispose()

    _publish_result(
        organization_id=candidate.organization_id,
        attempt_id=attempt_id,
        outcome="succeeded",
    )

    return LineageInvalidationExecutionCycleResult(
        status=LineageInvalidationExecutionCycleStatus.SUCCEEDED,
        candidate=candidate,
        attempt_id=attempt_id,
    )