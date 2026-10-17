from __future__ import annotations

from datetime import timedelta
import os
import threading
import time
import uuid

import pytest
from sqlalchemy import delete, func, select

from src.core.database import SessionLocal
from src.domain.lineage_invalidation import (
    LineageInvalidationDecision,
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_execution_target import (
    LineageExecutionTargetBinding,
    LineageExecutionTargetIdentity,
)
from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.domain.lineage_revision import LineageRevisionIdentity
from src.models.lineage_invalidation_decision import (
    LineageInvalidationDecisionRecord,
)
from src.models.lineage_invalidation_execution_attempt import (
    LineageInvalidationExecutionAttemptRecord,
)
from src.models.lineage_invalidation_execution_intent import (
    LineageInvalidationExecutionIntentRecord,
)
from src.models.lineage_invalidation_execution_result import (
    LineageInvalidationExecutionResultRecord,
)
from src.models.lineage_invalidation_policy_activation import (
    LineageInvalidationPolicyActivationRecord,
)
from src.models.lineage_execution_target_binding import (
    LineageExecutionTargetBindingRecord,
)
from src.models.lineage_observation import LineageObservationRecord
from src.models.organization import Organization
from src.services.lineage_invalidation_execution_claim_service import (
    LineageInvalidationExecutionClaimTerminalError,
    claim_lineage_invalidation_execution,
)
from src.services.lineage_invalidation_execution_intent_service import (
    authorize_lineage_invalidation_execution,
)
from src.services.lineage_invalidation_execution_result_service import (
    LineageInvalidationExecutionAttemptLeaseExpiredError,
    LineageInvalidationExecutionAttemptNotFoundError,
    LineageInvalidationExecutionAttemptSupersededError,
    LineageInvalidationExecutionResultConfigurationError,
    LineageInvalidationExecutionResultConflictError,
    LineageInvalidationExecutionResultNotEligibleError,
    persist_lineage_invalidation_execution_result,
)
from src.services.lineage_invalidation_persistence_service import (
    persist_lineage_invalidation_decision,
)
from src.services.lineage_invalidation_policy_activation_service import (
    activate_lineage_invalidation_policy,
)
from src.services.lineage_execution_target_binding_service import (
    persist_lineage_execution_target_binding,
)
from src.services.lineage_observation_persistence_service import (
    persist_lineage_observation,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        id=uuid.uuid4(),
        name=f"lineage-execution-result-{suffix}",
        slug=f"lineage-execution-result-{suffix}",
        billing_email=f"{suffix}@example.com",
    )
    db.add(organization)
    db.flush()
    return organization


def _revision(
    asset_external_id: str,
    revision_external_id: str,
    *,
    source_external_id: str = "source-a",
) -> LineageRevisionIdentity:
    return LineageRevisionIdentity(
        source_external_id=source_external_id,
        asset_external_id=asset_external_id,
        revision_external_id=revision_external_id,
    )


def _policy(
    revision: int,
) -> LineageInvalidationPolicyIdentity:
    return LineageInvalidationPolicyIdentity(
        policy_external_id="rag-invalidation",
        revision=revision,
    )


def _decision(
    *,
    policy_revision: int = 7,
    target_revision: str = "index-a-r7",
) -> LineageInvalidationDecision:
    root = _revision(
        "chunk-a",
        "chunk-a-r3",
    )
    target = _revision(
        "index-a",
        target_revision,
    )

    return LineageInvalidationDecision(
        root=root,
        target=target,
        policy=_policy(policy_revision),
        disposition="invalidate",
        reason_code="causal_dependency_changed",
        causal_path=(
            root,
            target,
        ),
    )


def _materialization(
    *,
    revision_external_id: str = "index-a-r7",
    sequence: int = 7,
) -> LineageObservation:
    return LineageObservation(
        observation_kind="materialization",
        source_external_id="source-a",
        snapshot_external_id=f"snapshot-{sequence}",
        asset_external_id="index-a",
        asset_kind="index",
        revision_external_id=revision_external_id,
        sequence=sequence,
        dependencies=(
            LineageRevisionReference(
                asset_external_id="chunk-a",
                revision_external_id="chunk-a-r3",
                dependency_type="indexed_from",
            ),
        ),
    )


def _chunk_materialization() -> LineageObservation:
    return LineageObservation(
        observation_kind="materialization",
        source_external_id="source-a",
        snapshot_external_id="chunk-snapshot-3",
        asset_external_id="chunk-a",
        asset_kind="chunk",
        revision_external_id="chunk-a-r3",
        sequence=3,
        dependencies=(),
    )


def _execution_target_binding(
    *,
    binding_revision: int = 1,
    document_id: str = "document-index-a",
) -> LineageExecutionTargetBinding:
    return LineageExecutionTargetBinding(
        identity=LineageExecutionTargetIdentity(
            source_external_id="source-a",
            asset_external_id="index-a",
        ),
        binding_revision=binding_revision,
        provider="pgvector",
        target_kind="langchain_pgvector_causal_chunk",
        configuration={
            "connection_ref": "test-downstream",
            "collection_name": (
                "rag-index" if document_id == "document-index-a"
                else "rag-index-v2"
            ),
        },
    )


def _establish_claimable_intent(
    db,
    *,
    organization_id: uuid.UUID,
) -> LineageInvalidationExecutionIntentRecord:
    persisted = persist_lineage_invalidation_decision(
        db,
        organization_id=organization_id,
        decision=_decision(),
    )

    activate_lineage_invalidation_policy(
        db,
        organization_id=organization_id,
        policy=_policy(7),
    )

    persist_lineage_observation(
        db,
        organization_id=organization_id,
        observation=_materialization(),
    )

    persist_lineage_observation(
        db,
        organization_id=organization_id,
        observation=_chunk_materialization(),
    )

    persist_lineage_execution_target_binding(
        db,
        organization_id=organization_id,
        binding=_execution_target_binding(),
    )

    intent = authorize_lineage_invalidation_execution(
        db,
        organization_id=organization_id,
        decision_record_id=persisted.record.id,
    )

    return intent.record


def _result_count(
    db,
    *,
    organization_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> int:
    return (
        db.query(func.count(LineageInvalidationExecutionResultRecord.id))
        .filter(
            LineageInvalidationExecutionResultRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionResultRecord.attempt_id == attempt_id,
        )
        .scalar()
    )


def _cleanup(
    organization_ids: list[uuid.UUID],
) -> None:
    if not organization_ids:
        return

    db = SessionLocal()

    try:
        db.rollback()
        db.execute(
            delete(LineageInvalidationExecutionResultRecord).where(
                LineageInvalidationExecutionResultRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(LineageInvalidationExecutionAttemptRecord).where(
                LineageInvalidationExecutionAttemptRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(LineageInvalidationExecutionIntentRecord).where(
                LineageInvalidationExecutionIntentRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(LineageInvalidationDecisionRecord).where(
                LineageInvalidationDecisionRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(LineageInvalidationPolicyActivationRecord).where(
                LineageInvalidationPolicyActivationRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(LineageObservationRecord).where(
                LineageObservationRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(LineageExecutionTargetBindingRecord).where(
                LineageExecutionTargetBindingRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(Organization).where(
                Organization.id.in_(organization_ids)
            )
        )
        db.commit()
    finally:
        db.close()


def test_fresh_current_attempt_can_publish_success() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )
        db.commit()

        before_result = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        result = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
            outcome="succeeded",
        )

        after_result = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        assert result.created is True
        assert result.record.organization_id == organization.id
        assert result.record.attempt_id == claim.record.id
        assert result.record.outcome == "succeeded"
        assert before_result <= result.record.recorded_at <= after_result
        assert _result_count(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
        ) == 1
    finally:
        db.close()
        _cleanup(organization_ids)


def test_fresh_current_attempt_can_publish_failure() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )
        db.commit()

        result = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
            outcome="failed",
        )

        assert result.created is True
        assert result.record.outcome == "failed"
    finally:
        db.close()
        _cleanup(organization_ids)


def test_same_result_is_idempotent() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )
        db.commit()

        first = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
            outcome="succeeded",
        )
        db.commit()

        second = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
            outcome="succeeded",
        )

        assert first.record.id == second.record.id
        assert second.created is False
        assert _result_count(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
        ) == 1
    finally:
        db.close()
        _cleanup(organization_ids)


def test_same_result_replay_is_idempotent_after_lease_expiration() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )
        db.commit()

        first = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
            outcome="succeeded",
        )
        db.commit()

        db.execute(
            LineageInvalidationExecutionAttemptRecord.__table__.update()
            .where(
                LineageInvalidationExecutionAttemptRecord.id
                == claim.record.id
            )
            .values(
                claimed_at=func.clock_timestamp() - timedelta(seconds=600),
                lease_expires_at=(
                    func.clock_timestamp() - timedelta(seconds=300)
                ),
            )
        )
        db.commit()

        replay = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
            outcome="succeeded",
        )

        assert replay.created is False
        assert replay.record.id == first.record.id
        assert replay.record.outcome == "succeeded"
        assert _result_count(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
        ) == 1
    finally:
        db.close()
        _cleanup(organization_ids)


def test_conflicting_result_replay_conflicts_after_lease_expiration() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )
        db.commit()

        first = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
            outcome="succeeded",
        )
        db.commit()

        db.execute(
            LineageInvalidationExecutionAttemptRecord.__table__.update()
            .where(
                LineageInvalidationExecutionAttemptRecord.id
                == claim.record.id
            )
            .values(
                claimed_at=func.clock_timestamp() - timedelta(seconds=600),
                lease_expires_at=(
                    func.clock_timestamp() - timedelta(seconds=300)
                ),
            )
        )
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutionResultConflictError
        ) as exc_info:
            persist_lineage_invalidation_execution_result(
                db,
                organization_id=organization.id,
                attempt_id=claim.record.id,
                outcome="failed",
            )

        assert exc_info.value.existing_outcome == "succeeded"
        assert exc_info.value.requested_outcome == "failed"
        assert _result_count(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
        ) == 1

        persisted = (
            db.query(LineageInvalidationExecutionResultRecord)
            .filter(
                LineageInvalidationExecutionResultRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionResultRecord.attempt_id
                == claim.record.id,
            )
            .one()
        )

        assert persisted.id == first.record.id
        assert persisted.outcome == "succeeded"
    finally:
        db.close()
        _cleanup(organization_ids)


def test_same_result_replay_is_idempotent_after_policy_advance() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )
        db.commit()

        first = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
            outcome="succeeded",
        )
        db.commit()

        activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(8),
        )
        db.commit()

        replay = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
            outcome="succeeded",
        )

        assert replay.created is False
        assert replay.record.id == first.record.id
        assert replay.record.outcome == "succeeded"
        assert _result_count(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
        ) == 1
    finally:
        db.close()
        _cleanup(organization_ids)


def test_different_result_conflicts() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )
        db.commit()

        persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
            outcome="succeeded",
        )
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutionResultConflictError
        ) as exc_info:
            persist_lineage_invalidation_execution_result(
                db,
                organization_id=organization.id,
                attempt_id=claim.record.id,
                outcome="failed",
            )

        assert exc_info.value.existing_outcome == "succeeded"
        assert exc_info.value.requested_outcome == "failed"
        assert _result_count(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
        ) == 1
    finally:
        db.close()
        _cleanup(organization_ids)


def test_result_lookup_is_tenant_scoped() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        owner = _make_organization(db)
        other = _make_organization(db)
        organization_ids.extend(
            [
                owner.id,
                other.id,
            ]
        )

        intent = _establish_claimable_intent(
            db,
            organization_id=owner.id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=owner.id,
            intent_id=intent.id,
        )
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutionAttemptNotFoundError
        ):
            persist_lineage_invalidation_execution_result(
                db,
                organization_id=other.id,
                attempt_id=claim.record.id,
                outcome="succeeded",
            )

        assert _result_count(
            db,
            organization_id=owner.id,
            attempt_id=claim.record.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_invalid_outcome_is_rejected() -> None:
    db = SessionLocal()

    try:
        with pytest.raises(
            LineageInvalidationExecutionResultConfigurationError
        ):
            persist_lineage_invalidation_execution_result(
                db,
                organization_id=uuid.uuid4(),
                attempt_id=uuid.uuid4(),
                outcome="running",  # type: ignore[arg-type]
            )
    finally:
        db.close()


def test_caller_rollback_removes_created_result() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        db.commit()

        result = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
            outcome="succeeded",
        )

        assert result.created is True
        assert _result_count(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
        ) == 1

        db.rollback()

        assert _result_count(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_result_service_does_not_commit_caller_transaction() -> None:
    db = SessionLocal()
    observer = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        db.commit()

        result = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
            outcome="succeeded",
        )

        assert result.created is True
        assert _result_count(
            observer,
            organization_id=organization.id,
            attempt_id=claim.record.id,
        ) == 0

        db.commit()
        observer.rollback()

        assert _result_count(
            observer,
            organization_id=organization.id,
            attempt_id=claim.record.id,
        ) == 1
    finally:
        observer.close()
        db.close()
        _cleanup(organization_ids)


def test_expired_attempt_cannot_publish_result() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )
        db.commit()

        db_now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        attempt = LineageInvalidationExecutionAttemptRecord(
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            binding_revision=1,
            claimed_at=db_now - timedelta(seconds=600),
            lease_expires_at=db_now - timedelta(seconds=300),
        )
        db.add(attempt)
        db.commit()

        attempt_id = attempt.id

        with pytest.raises(
            LineageInvalidationExecutionAttemptLeaseExpiredError
        ) as exc_info:
            persist_lineage_invalidation_execution_result(
                db,
                organization_id=organization.id,
                attempt_id=attempt_id,
                outcome="succeeded",
            )

        assert exc_info.value.lease_expires_at == attempt.lease_expires_at
        assert _result_count(
            db,
            organization_id=organization.id,
            attempt_id=attempt_id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_superseded_attempt_cannot_publish_result() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )
        db.commit()

        db_now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        first = LineageInvalidationExecutionAttemptRecord(
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            binding_revision=1,
            claimed_at=db_now - timedelta(seconds=600),
            lease_expires_at=db_now - timedelta(seconds=300),
        )
        db.add(first)
        db.commit()

        first_id = first.id

        second = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutionAttemptSupersededError
        ) as exc_info:
            persist_lineage_invalidation_execution_result(
                db,
                organization_id=organization.id,
                attempt_id=first_id,
                outcome="succeeded",
            )

        assert exc_info.value.current_attempt_id == second.record.id
        assert _result_count(
            db,
            organization_id=organization.id,
            attempt_id=first_id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_policy_advance_blocks_result_publication() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        db.commit()

        activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(8),
        )
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutionResultNotEligibleError
        ) as exc_info:
            persist_lineage_invalidation_execution_result(
                db,
                organization_id=organization.id,
                attempt_id=claim.record.id,
                outcome="succeeded",
            )

        assert exc_info.value.reason_code == "policy_revision_superseded"
        assert _result_count(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_materialization_advance_blocks_result_publication() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        db.commit()

        persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_materialization(
                revision_external_id="index-a-r8",
                sequence=8,
            ),
        )
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutionResultNotEligibleError
        ) as exc_info:
            persist_lineage_invalidation_execution_result(
                db,
                organization_id=organization.id,
                attempt_id=claim.record.id,
                outcome="succeeded",
            )

        assert exc_info.value.reason_code == "target_not_current"
        assert _result_count(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_result_waits_for_policy_authority_writer_and_revalidates() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            setup_db,
            organization_id=organization.id,
        )
        setup_db.commit()

        claim = claim_lineage_invalidation_execution(
            setup_db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        setup_db.commit()

        organization_id = organization.id
        attempt_id = claim.record.id

        writer_has_lock = threading.Event()
        allow_writer_commit = threading.Event()
        result_started = threading.Event()
        result_finished = threading.Event()

        lock = threading.Lock()
        result_reasons: list[str] = []
        errors: list[BaseException] = []

        def advance_policy() -> None:
            db = SessionLocal()

            try:
                from src.services.lineage_authority_lock_service import (
                    acquire_lineage_policy_authority_lock,
                )

                acquire_lineage_policy_authority_lock(
                    db,
                    organization_id=organization_id,
                    policy_external_id="rag-invalidation",
                )

                activate_lineage_invalidation_policy(
                    db,
                    organization_id=organization_id,
                    policy=_policy(8),
                )

                writer_has_lock.set()

                if not allow_writer_commit.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out waiting to release policy writer."
                    )

                db.commit()
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                writer_has_lock.set()
                db.close()

        def publish_result() -> None:
            db = SessionLocal()

            try:
                if not writer_has_lock.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out waiting for policy authority lock."
                    )

                result_started.set()

                try:
                    persist_lineage_invalidation_execution_result(
                        db,
                        organization_id=organization_id,
                        attempt_id=attempt_id,
                        outcome="succeeded",
                    )
                    db.commit()

                    with lock:
                        errors.append(
                            AssertionError(
                                "Result unexpectedly succeeded after "
                                "policy authority advanced."
                            )
                        )
                except LineageInvalidationExecutionResultNotEligibleError as exc:
                    db.rollback()

                    with lock:
                        result_reasons.append(exc.reason_code)
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                result_finished.set()
                db.close()

        writer_thread = threading.Thread(target=advance_policy)
        result_thread = threading.Thread(target=publish_result)

        writer_thread.start()

        assert writer_has_lock.wait(timeout=10)

        result_thread.start()

        assert result_started.wait(timeout=10)
        assert not result_finished.wait(timeout=0.25)

        allow_writer_commit.set()

        writer_thread.join(timeout=10)
        result_thread.join(timeout=10)

        assert not writer_thread.is_alive()
        assert not result_thread.is_alive()
        assert errors == []
        assert result_reasons == ["policy_revision_superseded"]

        verify_db = SessionLocal()

        try:
            assert _result_count(
                verify_db,
                organization_id=organization_id,
                attempt_id=attempt_id,
            ) == 0
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_result_uses_post_wait_db_clock_for_lease_expiration() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            setup_db,
            organization_id=organization.id,
        )
        setup_db.commit()

        seed_now = setup_db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        attempt = LineageInvalidationExecutionAttemptRecord(
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            binding_revision=1,
            claimed_at=seed_now - timedelta(seconds=300),
            lease_expires_at=seed_now + timedelta(seconds=1),
        )
        setup_db.add(attempt)
        setup_db.commit()

        organization_id = organization.id
        attempt_id = attempt.id
        lease_expires_at = attempt.lease_expires_at

        writer_has_lock = threading.Event()
        allow_writer_commit = threading.Event()
        result_started = threading.Event()
        result_finished = threading.Event()

        lock = threading.Lock()
        expired_leases = []
        errors: list[BaseException] = []

        def hold_policy_authority() -> None:
            db = SessionLocal()

            try:
                from src.services.lineage_authority_lock_service import (
                    acquire_lineage_policy_authority_lock,
                )

                acquire_lineage_policy_authority_lock(
                    db,
                    organization_id=organization_id,
                    policy_external_id="rag-invalidation",
                )

                writer_has_lock.set()

                if not allow_writer_commit.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out waiting to release policy authority."
                    )

                db.commit()
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                writer_has_lock.set()
                db.close()

        def publish_result() -> None:
            db = SessionLocal()

            try:
                if not writer_has_lock.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out waiting for policy authority lock."
                    )

                result_started.set()

                try:
                    persist_lineage_invalidation_execution_result(
                        db,
                        organization_id=organization_id,
                        attempt_id=attempt_id,
                        outcome="succeeded",
                    )
                    db.commit()

                    with lock:
                        errors.append(
                            AssertionError(
                                "Result unexpectedly succeeded after "
                                "the execution lease expired."
                            )
                        )
                except LineageInvalidationExecutionAttemptLeaseExpiredError as exc:
                    db.rollback()

                    with lock:
                        expired_leases.append(exc.lease_expires_at)
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                result_finished.set()
                db.close()

        writer_thread = threading.Thread(target=hold_policy_authority)
        result_thread = threading.Thread(target=publish_result)

        writer_thread.start()

        assert writer_has_lock.wait(timeout=10)

        result_thread.start()

        assert result_started.wait(timeout=10)
        assert not result_finished.wait(timeout=0.25)

        while True:
            current_db_time = setup_db.execute(
                select(func.clock_timestamp())
            ).scalar_one()

            if current_db_time >= lease_expires_at:
                break

            time.sleep(0.05)

        allow_writer_commit.set()

        writer_thread.join(timeout=10)
        result_thread.join(timeout=10)

        assert not writer_thread.is_alive()
        assert not result_thread.is_alive()
        assert errors == []
        assert expired_leases == [lease_expires_at]

        verify_db = SessionLocal()

        try:
            assert _result_count(
                verify_db,
                organization_id=organization_id,
                attempt_id=attempt_id,
            ) == 0
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_reclaim_wins_before_late_result_and_fences_old_attempt() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            setup_db,
            organization_id=organization.id,
        )
        setup_db.commit()

        seed_now = setup_db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        first = LineageInvalidationExecutionAttemptRecord(
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            binding_revision=1,
            claimed_at=seed_now - timedelta(seconds=600),
            lease_expires_at=seed_now - timedelta(seconds=300),
        )
        setup_db.add(first)
        setup_db.commit()

        organization_id = organization.id
        intent_id = intent.id
        first_attempt_id = first.id

        reclaim_has_intent_lock = threading.Event()
        allow_reclaim_commit = threading.Event()
        result_started = threading.Event()
        result_finished = threading.Event()

        lock = threading.Lock()
        reclaimed_attempts: list[tuple[uuid.UUID, int]] = []
        superseding_attempt_ids: list[uuid.UUID] = []
        errors: list[BaseException] = []

        def reclaim() -> None:
            db = SessionLocal()

            try:
                result = claim_lineage_invalidation_execution(
                    db,
                    organization_id=organization_id,
                    intent_id=intent_id,
                    lease_seconds=300,
                )

                attempt_id = result.record.id
                attempt_number = result.record.attempt_number

                reclaim_has_intent_lock.set()

                if not allow_reclaim_commit.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out waiting to commit execution reclaim."
                    )

                db.commit()

                with lock:
                    reclaimed_attempts.append(
                        (
                            attempt_id,
                            attempt_number,
                        )
                    )
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                reclaim_has_intent_lock.set()
                db.close()

        def publish_late_result() -> None:
            db = SessionLocal()

            try:
                if not reclaim_has_intent_lock.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out waiting for reclaim intent lock."
                    )

                result_started.set()

                try:
                    persist_lineage_invalidation_execution_result(
                        db,
                        organization_id=organization_id,
                        attempt_id=first_attempt_id,
                        outcome="succeeded",
                    )
                    db.commit()

                    with lock:
                        errors.append(
                            AssertionError(
                                "Superseded execution attempt unexpectedly "
                                "published an authoritative result."
                            )
                        )
                except LineageInvalidationExecutionAttemptSupersededError as exc:
                    db.rollback()

                    with lock:
                        superseding_attempt_ids.append(
                            exc.current_attempt_id
                        )
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                result_finished.set()
                db.close()

        reclaim_thread = threading.Thread(target=reclaim)
        result_thread = threading.Thread(target=publish_late_result)

        reclaim_thread.start()

        assert reclaim_has_intent_lock.wait(timeout=10)

        result_thread.start()

        assert result_started.wait(timeout=10)
        assert not result_finished.wait(timeout=0.25)

        allow_reclaim_commit.set()

        reclaim_thread.join(timeout=10)
        result_thread.join(timeout=10)

        assert not reclaim_thread.is_alive()
        assert not result_thread.is_alive()
        assert errors == []
        assert len(reclaimed_attempts) == 1

        second_attempt_id, second_attempt_number = reclaimed_attempts[0]

        assert second_attempt_number == 2
        assert superseding_attempt_ids == [second_attempt_id]

        verify_db = SessionLocal()

        try:
            assert _result_count(
                verify_db,
                organization_id=organization_id,
                attempt_id=first_attempt_id,
            ) == 0

            attempts = (
                verify_db.query(
                    LineageInvalidationExecutionAttemptRecord
                )
                .filter(
                    LineageInvalidationExecutionAttemptRecord.organization_id
                    == organization_id,
                    LineageInvalidationExecutionAttemptRecord.intent_id
                    == intent_id,
                )
                .order_by(
                    LineageInvalidationExecutionAttemptRecord.attempt_number
                    .asc()
                )
                .all()
            )

            assert len(attempts) == 2
            assert attempts[0].id == first_attempt_id
            assert attempts[0].attempt_number == 1
            assert attempts[1].id == second_attempt_id
            assert attempts[1].attempt_number == 2
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_result_wins_before_reclaim_and_terminally_blocks_reclaim() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            setup_db,
            organization_id=organization.id,
        )
        setup_db.commit()

        seed_now = setup_db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        first = LineageInvalidationExecutionAttemptRecord(
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            binding_revision=1,
            claimed_at=seed_now - timedelta(seconds=300),
            lease_expires_at=seed_now + timedelta(seconds=1),
        )
        setup_db.add(first)
        setup_db.commit()

        organization_id = organization.id
        intent_id = intent.id
        first_attempt_id = first.id
        lease_expires_at = first.lease_expires_at

        result_has_intent_lock = threading.Event()
        allow_result_commit = threading.Event()
        claim_started = threading.Event()
        claim_finished = threading.Event()

        lock = threading.Lock()
        recorded_result_ids: list[uuid.UUID] = []
        terminal_attempts: list[tuple[uuid.UUID, str]] = []
        errors: list[BaseException] = []

        def publish_result() -> None:
            db = SessionLocal()

            try:
                result = persist_lineage_invalidation_execution_result(
                    db,
                    organization_id=organization_id,
                    attempt_id=first_attempt_id,
                    outcome="succeeded",
                )

                result_id = result.record.id

                result_has_intent_lock.set()

                if not allow_result_commit.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out waiting to commit execution result."
                    )

                db.commit()

                with lock:
                    recorded_result_ids.append(result_id)
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                result_has_intent_lock.set()
                db.close()

        def reclaim() -> None:
            db = SessionLocal()

            try:
                if not result_has_intent_lock.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out waiting for result intent lock."
                    )

                claim_started.set()

                try:
                    claim_lineage_invalidation_execution(
                        db,
                        organization_id=organization_id,
                        intent_id=intent_id,
                        lease_seconds=300,
                    )
                    db.commit()

                    with lock:
                        errors.append(
                            AssertionError(
                                "Execution intent with a terminal result "
                                "unexpectedly allowed reclaim."
                            )
                        )
                except LineageInvalidationExecutionClaimTerminalError as exc:
                    db.rollback()

                    with lock:
                        terminal_attempts.append(
                            (
                                exc.current_attempt_id,
                                exc.outcome,
                            )
                        )
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                claim_finished.set()
                db.close()

        result_thread = threading.Thread(target=publish_result)
        claim_thread = threading.Thread(target=reclaim)

        result_thread.start()

        assert result_has_intent_lock.wait(timeout=10)

        claim_thread.start()

        assert claim_started.wait(timeout=10)
        assert not claim_finished.wait(timeout=0.25)

        while True:
            current_db_time = setup_db.execute(
                select(func.clock_timestamp())
            ).scalar_one()

            if current_db_time >= lease_expires_at:
                break

            time.sleep(0.05)

        allow_result_commit.set()

        result_thread.join(timeout=10)
        claim_thread.join(timeout=10)

        assert not result_thread.is_alive()
        assert not claim_thread.is_alive()
        assert errors == []
        assert len(recorded_result_ids) == 1
        assert terminal_attempts == [
            (
                first_attempt_id,
                "succeeded",
            )
        ]

        verify_db = SessionLocal()

        try:
            attempts = (
                verify_db.query(
                    LineageInvalidationExecutionAttemptRecord
                )
                .filter(
                    LineageInvalidationExecutionAttemptRecord.organization_id
                    == organization_id,
                    LineageInvalidationExecutionAttemptRecord.intent_id
                    == intent_id,
                )
                .order_by(
                    LineageInvalidationExecutionAttemptRecord.attempt_number
                    .asc()
                )
                .all()
            )

            assert len(attempts) == 1
            assert attempts[0].id == first_attempt_id
            assert attempts[0].attempt_number == 1

            results = (
                verify_db.query(
                    LineageInvalidationExecutionResultRecord
                )
                .filter(
                    LineageInvalidationExecutionResultRecord.organization_id
                    == organization_id,
                    LineageInvalidationExecutionResultRecord.attempt_id
                    == first_attempt_id,
                )
                .all()
            )

            assert len(results) == 1
            assert results[0].id == recorded_result_ids[0]
            assert results[0].outcome == "succeeded"
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)