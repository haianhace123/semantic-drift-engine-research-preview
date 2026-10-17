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
from src.services.lineage_authority_lock_service import (
    acquire_lineage_policy_authority_lock,
    acquire_lineage_target_authority_lock,
)
from src.services.lineage_invalidation_execution_claim_service import (
    LineageInvalidationExecutionClaimConfigurationError,
    LineageInvalidationExecutionClaimNotEligibleError,
    LineageInvalidationExecutionClaimTerminalError,
    LineageInvalidationExecutionClaimUnavailableError,
    LineageInvalidationExecutionIntentNotFoundError,
    LineageInvalidationExecutionTargetBindingNotFoundError,
    LineageInvalidationExecutionUnsafeTargetError,
    claim_lineage_invalidation_execution,
    LineageInvalidationExecutionRetryDeferredError,
)
from src.services.lineage_invalidation_execution_intent_service import (
    authorize_lineage_invalidation_execution,
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
        name=f"lineage-execution-claim-{suffix}",
        slug=f"lineage-execution-claim-{suffix}",
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
    collection_name: str = "rag-index",
    legacy_document_scoped: bool = False,
) -> LineageExecutionTargetBinding:
    if legacy_document_scoped:
        # Explicit legacy binding: tests that this unsafe contract is refused.
        configuration = {
            "connection_ref": "test-downstream",
            "collection_name": collection_name,
            "document_id": "document-index-a",
        }
    else:
        configuration = {
            "connection_ref": "test-downstream",
            "collection_name": collection_name,
        }

    return LineageExecutionTargetBinding(
        identity=LineageExecutionTargetIdentity(
            source_external_id="source-a",
            asset_external_id="index-a",
        ),
        binding_revision=binding_revision,
        provider="pgvector",
        target_kind=(
            "langchain_pgvector_causal_chunk"
            if not legacy_document_scoped
            else "langchain_pgvector_document"
        ),
        configuration=configuration,
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


def _attempt_count(
    db,
    *,
    organization_id: uuid.UUID,
    intent_id: uuid.UUID,
) -> int:
    return (
        db.query(func.count(LineageInvalidationExecutionAttemptRecord.id))
        .filter(
            LineageInvalidationExecutionAttemptRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionAttemptRecord.intent_id
            == intent_id,
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


def test_claim_rejects_document_scoped_target_without_attempt() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )

        persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_execution_target_binding(
                binding_revision=2,
                legacy_document_scoped=True,
            ),
        )
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutionUnsafeTargetError
        ):
            claim_lineage_invalidation_execution(
                db,
                organization_id=organization.id,
                intent_id=intent.id,
            )

        assert _attempt_count(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_first_claim_creates_attempt_one() -> None:
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

        before_claim = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        result = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )

        after_claim = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        assert result.record.id is not None
        assert result.record.organization_id == organization.id
        assert result.record.intent_id == intent.id
        assert result.record.attempt_number == 1
        assert result.record.binding_revision == 1
        assert before_claim <= result.record.claimed_at <= after_claim
        assert result.record.lease_expires_at == (
            result.record.claimed_at + timedelta(seconds=300)
        )
        assert _attempt_count(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        ) == 1
    finally:
        db.close()
        _cleanup(organization_ids)


def test_first_claim_pins_current_execution_target_binding_revision() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )

        persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_execution_target_binding(
                binding_revision=2,
                collection_name="rag-index-v2",
            ),
        )
        db.commit()

        result = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )

        assert result.record.binding_revision == 2
    finally:
        db.close()
        _cleanup(organization_ids)


def test_claim_fails_closed_when_execution_target_binding_is_missing() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        persisted = persist_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            decision=_decision(),
        )
        activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(7),
        )
        persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_materialization(),
        )
        intent = authorize_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        ).record
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutionTargetBindingNotFoundError
        ):
            claim_lineage_invalidation_execution(
                db,
                organization_id=organization.id,
                intent_id=intent.id,
            )

        assert _attempt_count(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_fresh_attempt_blocks_reclaim() -> None:
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

        first = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutionClaimUnavailableError
        ) as exc_info:
            claim_lineage_invalidation_execution(
                db,
                organization_id=organization.id,
                intent_id=intent.id,
                lease_seconds=300,
            )

        assert exc_info.value.current_attempt_id == first.record.id
        assert exc_info.value.lease_expires_at == (
            first.record.lease_expires_at
        )
        assert _attempt_count(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        ) == 1
    finally:
        db.close()
        _cleanup(organization_ids)


def test_expired_lease_allows_reclaim() -> None:
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
        db.flush()

        first_id = first.id

        second = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )

        assert second.record.id != first_id
        assert second.record.attempt_number == 2
        assert second.record.claimed_at > first.lease_expires_at
        assert second.record.lease_expires_at == (
            second.record.claimed_at + timedelta(seconds=300)
        )
        assert _attempt_count(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        ) == 2
    finally:
        db.close()
        _cleanup(organization_ids)


@pytest.mark.parametrize(
    "outcome",
    (
        "succeeded",
        "failed",
    ),
)
def test_terminal_result_blocks_reclaim_after_lease_expiration(
    outcome: str,
) -> None:
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
        db.flush()

        terminal_result = LineageInvalidationExecutionResultRecord(
            organization_id=organization.id,
            attempt_id=first.id,
            outcome=outcome,
            recorded_at=db_now - timedelta(seconds=400),
        )
        db.add(terminal_result)
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutionClaimTerminalError
        ) as exc_info:
            claim_lineage_invalidation_execution(
                db,
                organization_id=organization.id,
                intent_id=intent.id,
                lease_seconds=300,
            )

        assert exc_info.value.current_attempt_id == first.id
        assert exc_info.value.outcome == outcome
        assert _attempt_count(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        ) == 1
    finally:
        db.close()
        _cleanup(organization_ids)


def test_claim_revalidates_policy_authority() -> None:
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

        activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(8),
        )
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutionClaimNotEligibleError
        ) as exc_info:
            claim_lineage_invalidation_execution(
                db,
                organization_id=organization.id,
                intent_id=intent.id,
            )

        assert exc_info.value.reason_code == "policy_revision_superseded"
        assert _attempt_count(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_claim_revalidates_target_authority() -> None:
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
            LineageInvalidationExecutionClaimNotEligibleError
        ) as exc_info:
            claim_lineage_invalidation_execution(
                db,
                organization_id=organization.id,
                intent_id=intent.id,
            )

        assert exc_info.value.reason_code == "target_not_current"
        assert _attempt_count(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_claim_lookup_is_tenant_scoped() -> None:
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

        with pytest.raises(
            LineageInvalidationExecutionIntentNotFoundError
        ):
            claim_lineage_invalidation_execution(
                db,
                organization_id=other.id,
                intent_id=intent.id,
            )

        assert _attempt_count(
            db,
            organization_id=owner.id,
            intent_id=intent.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_caller_rollback_removes_created_attempt() -> None:
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

        result = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )

        assert result.record.attempt_number == 1
        assert _attempt_count(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        ) == 1

        db.rollback()

        assert _attempt_count(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_claim_does_not_commit_caller_transaction() -> None:
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

        result = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )

        assert result.record.attempt_number == 1

        observer_count = _attempt_count(
            observer,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        assert observer_count == 0

        db.commit()
        observer.rollback()

        observer_count = _attempt_count(
            observer,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        assert observer_count == 1
    finally:
        observer.close()
        db.close()
        _cleanup(organization_ids)


@pytest.mark.parametrize(
    "lease_seconds",
    (
        0,
        -1,
    ),
)
def test_invalid_lease_seconds_are_rejected(
    lease_seconds: int,
) -> None:
    db = SessionLocal()

    try:
        with pytest.raises(
            LineageInvalidationExecutionClaimConfigurationError
        ):
            claim_lineage_invalidation_execution(
                db,
                organization_id=uuid.uuid4(),
                intent_id=uuid.uuid4(),
                lease_seconds=lease_seconds,
            )
    finally:
        db.close()


def test_concurrent_first_claim_creates_one_current_attempt() -> None:
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

        organization_id = organization.id
        intent_id = intent.id

        barrier = threading.Barrier(2)
        lock = threading.Lock()
        claimed_attempts: list[tuple[uuid.UUID, int]] = []
        unavailable_attempt_ids: list[uuid.UUID] = []
        errors: list[BaseException] = []

        def claim() -> None:
            db = SessionLocal()

            try:
                barrier.wait()

                try:
                    result = claim_lineage_invalidation_execution(
                        db,
                        organization_id=organization_id,
                        intent_id=intent_id,
                        lease_seconds=300,
                    )
                    attempt_id = result.record.id
                    attempt_number = result.record.attempt_number
                    db.commit()

                    with lock:
                        claimed_attempts.append(
                            (
                                attempt_id,
                                attempt_number,
                            )
                        )
                except LineageInvalidationExecutionClaimUnavailableError as exc:
                    current_attempt_id = exc.current_attempt_id
                    db.rollback()

                    with lock:
                        unavailable_attempt_ids.append(
                            current_attempt_id
                        )
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                db.close()

        threads = [
            threading.Thread(target=claim),
            threading.Thread(target=claim),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=10)

        assert all(
            not thread.is_alive()
            for thread in threads
        )
        assert errors == []
        assert len(claimed_attempts) == 1
        assert len(unavailable_attempt_ids) == 1

        claimed_attempt_id, attempt_number = claimed_attempts[0]

        assert attempt_number == 1
        assert unavailable_attempt_ids == [claimed_attempt_id]

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
            assert attempts[0].id == claimed_attempt_id
            assert attempts[0].attempt_number == 1
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_policy_advance_and_claim_are_linearizable() -> None:
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

        organization_id = organization.id
        intent_id = intent.id

        barrier = threading.Barrier(2)
        lock = threading.Lock()
        claimed_attempt_ids: list[uuid.UUID] = []
        claim_reasons: list[str] = []
        errors: list[BaseException] = []

        def claim() -> None:
            db = SessionLocal()

            try:
                barrier.wait()

                try:
                    result = claim_lineage_invalidation_execution(
                        db,
                        organization_id=organization_id,
                        intent_id=intent_id,
                    )
                    attempt_id = result.record.id
                    db.commit()

                    with lock:
                        claimed_attempt_ids.append(attempt_id)
                except LineageInvalidationExecutionClaimNotEligibleError as exc:
                    db.rollback()

                    with lock:
                        claim_reasons.append(exc.reason_code)
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                db.close()

        def advance_policy() -> None:
            db = SessionLocal()

            try:
                barrier.wait()

                activate_lineage_invalidation_policy(
                    db,
                    organization_id=organization_id,
                    policy=_policy(8),
                )
                db.commit()
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                db.close()

        threads = [
            threading.Thread(target=claim),
            threading.Thread(target=advance_policy),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=10)

        assert all(
            not thread.is_alive()
            for thread in threads
        )
        assert errors == []

        assert (
            (
                len(claimed_attempt_ids) == 1
                and claim_reasons == []
            )
            or (
                claimed_attempt_ids == []
                and claim_reasons == ["policy_revision_superseded"]
            )
        )

        verify_db = SessionLocal()

        try:
            attempt_count = _attempt_count(
                verify_db,
                organization_id=organization_id,
                intent_id=intent_id,
            )

            if claimed_attempt_ids:
                assert attempt_count == 1
            else:
                assert attempt_count == 0
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_materialization_advance_and_claim_are_linearizable() -> None:
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

        organization_id = organization.id
        intent_id = intent.id

        barrier = threading.Barrier(2)
        lock = threading.Lock()
        claimed_attempt_ids: list[uuid.UUID] = []
        claim_reasons: list[str] = []
        errors: list[BaseException] = []

        def claim() -> None:
            db = SessionLocal()

            try:
                barrier.wait()

                try:
                    result = claim_lineage_invalidation_execution(
                        db,
                        organization_id=organization_id,
                        intent_id=intent_id,
                    )
                    attempt_id = result.record.id
                    db.commit()

                    with lock:
                        claimed_attempt_ids.append(attempt_id)
                except LineageInvalidationExecutionClaimNotEligibleError as exc:
                    db.rollback()

                    with lock:
                        claim_reasons.append(exc.reason_code)
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                db.close()

        def advance_materialization() -> None:
            db = SessionLocal()

            try:
                barrier.wait()

                persist_lineage_observation(
                    db,
                    organization_id=organization_id,
                    observation=_materialization(
                        revision_external_id="index-a-r8",
                        sequence=8,
                    ),
                )
                db.commit()
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                db.close()

        threads = [
            threading.Thread(target=claim),
            threading.Thread(target=advance_materialization),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=10)

        assert all(
            not thread.is_alive()
            for thread in threads
        )
        assert errors == []

        assert (
            (
                len(claimed_attempt_ids) == 1
                and claim_reasons == []
            )
            or (
                claimed_attempt_ids == []
                and claim_reasons == ["target_not_current"]
            )
        )

        verify_db = SessionLocal()

        try:
            attempt_count = _attempt_count(
                verify_db,
                organization_id=organization_id,
                intent_id=intent_id,
            )

            if claimed_attempt_ids:
                assert attempt_count == 1
            else:
                assert attempt_count == 0
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_claim_waits_for_policy_authority_writer_and_revalidates() -> None:
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

        organization_id = organization.id
        intent_id = intent.id

        writer_has_lock = threading.Event()
        allow_writer_commit = threading.Event()
        claim_started = threading.Event()
        claim_finished = threading.Event()

        lock = threading.Lock()
        claim_reasons: list[str] = []
        errors: list[BaseException] = []

        def advance_policy() -> None:
            db = SessionLocal()

            try:
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

        def claim() -> None:
            db = SessionLocal()

            try:
                if not writer_has_lock.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out waiting for policy authority lock."
                    )

                claim_started.set()

                try:
                    claim_lineage_invalidation_execution(
                        db,
                        organization_id=organization_id,
                        intent_id=intent_id,
                    )
                    db.commit()

                    with lock:
                        errors.append(
                            AssertionError(
                                "Claim unexpectedly succeeded after "
                                "policy authority advanced."
                            )
                        )
                except LineageInvalidationExecutionClaimNotEligibleError as exc:
                    db.rollback()

                    with lock:
                        claim_reasons.append(exc.reason_code)
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                claim_finished.set()
                db.close()

        writer_thread = threading.Thread(target=advance_policy)
        claim_thread = threading.Thread(target=claim)

        writer_thread.start()

        assert writer_has_lock.wait(timeout=10)

        claim_thread.start()

        assert claim_started.wait(timeout=10)
        assert not claim_finished.wait(timeout=0.25)

        allow_writer_commit.set()

        writer_thread.join(timeout=10)
        claim_thread.join(timeout=10)

        assert not writer_thread.is_alive()
        assert not claim_thread.is_alive()
        assert errors == []
        assert claim_reasons == ["policy_revision_superseded"]

        verify_db = SessionLocal()

        try:
            assert _attempt_count(
                verify_db,
                organization_id=organization_id,
                intent_id=intent_id,
            ) == 0
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_claim_waits_for_target_authority_writer_and_revalidates() -> None:
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

        organization_id = organization.id
        intent_id = intent.id

        writer_has_lock = threading.Event()
        allow_writer_commit = threading.Event()
        claim_started = threading.Event()
        claim_finished = threading.Event()

        lock = threading.Lock()
        claim_reasons: list[str] = []
        errors: list[BaseException] = []

        def advance_materialization() -> None:
            db = SessionLocal()

            try:
                acquire_lineage_target_authority_lock(
                    db,
                    organization_id=organization_id,
                    source_external_id="source-a",
                    asset_external_id="index-a",
                )

                persist_lineage_observation(
                    db,
                    organization_id=organization_id,
                    observation=_materialization(
                        revision_external_id="index-a-r8",
                        sequence=8,
                    ),
                )

                writer_has_lock.set()

                if not allow_writer_commit.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out waiting to release target writer."
                    )

                db.commit()
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                writer_has_lock.set()
                db.close()

        def claim() -> None:
            db = SessionLocal()

            try:
                if not writer_has_lock.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out waiting for target authority lock."
                    )

                claim_started.set()

                try:
                    claim_lineage_invalidation_execution(
                        db,
                        organization_id=organization_id,
                        intent_id=intent_id,
                    )
                    db.commit()

                    with lock:
                        errors.append(
                            AssertionError(
                                "Claim unexpectedly succeeded after "
                                "target authority advanced."
                            )
                        )
                except LineageInvalidationExecutionClaimNotEligibleError as exc:
                    db.rollback()

                    with lock:
                        claim_reasons.append(exc.reason_code)
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                claim_finished.set()
                db.close()

        writer_thread = threading.Thread(target=advance_materialization)
        claim_thread = threading.Thread(target=claim)

        writer_thread.start()

        assert writer_has_lock.wait(timeout=10)

        claim_thread.start()

        assert claim_started.wait(timeout=10)
        assert not claim_finished.wait(timeout=0.25)

        allow_writer_commit.set()

        writer_thread.join(timeout=10)
        claim_thread.join(timeout=10)

        assert not writer_thread.is_alive()
        assert not claim_thread.is_alive()
        assert errors == []
        assert claim_reasons == ["target_not_current"]

        verify_db = SessionLocal()

        try:
            assert _attempt_count(
                verify_db,
                organization_id=organization_id,
                intent_id=intent_id,
            ) == 0
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_claim_waits_for_binding_writer_and_pins_committed_revision() -> None:
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

        organization_id = organization.id
        intent_id = intent.id

        writer_has_lock = threading.Event()
        allow_writer_commit = threading.Event()
        claim_started = threading.Event()
        claim_finished = threading.Event()

        lock = threading.Lock()
        claimed_attempts: list[tuple[uuid.UUID, int]] = []
        errors: list[BaseException] = []

        def advance_binding() -> None:
            db = SessionLocal()

            try:
                acquire_lineage_target_authority_lock(
                    db,
                    organization_id=organization_id,
                    source_external_id="source-a",
                    asset_external_id="index-a",
                )

                persist_lineage_execution_target_binding(
                    db,
                    organization_id=organization_id,
                    binding=_execution_target_binding(
                        binding_revision=2,
                        collection_name="rag-index-v2",
                    ),
                )

                writer_has_lock.set()

                if not allow_writer_commit.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out waiting to release binding writer."
                    )

                db.commit()
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                writer_has_lock.set()
                db.close()

        def claim() -> None:
            db = SessionLocal()

            try:
                if not writer_has_lock.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out waiting for binding authority lock."
                    )

                claim_started.set()

                result = claim_lineage_invalidation_execution(
                    db,
                    organization_id=organization_id,
                    intent_id=intent_id,
                )

                attempt_id = result.record.id
                binding_revision = result.record.binding_revision
                db.commit()

                with lock:
                    claimed_attempts.append(
                        (
                            attempt_id,
                            binding_revision,
                        )
                    )
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                claim_finished.set()
                db.close()

        writer_thread = threading.Thread(target=advance_binding)
        claim_thread = threading.Thread(target=claim)

        writer_thread.start()

        assert writer_has_lock.wait(timeout=10)

        claim_thread.start()

        assert claim_started.wait(timeout=10)
        assert not claim_finished.wait(timeout=0.25)

        allow_writer_commit.set()

        writer_thread.join(timeout=10)
        claim_thread.join(timeout=10)

        assert not writer_thread.is_alive()
        assert not claim_thread.is_alive()
        assert errors == []
        assert len(claimed_attempts) == 1

        claimed_attempt_id, binding_revision = claimed_attempts[0]

        assert binding_revision == 2

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
                .all()
            )

            assert len(attempts) == 1
            assert attempts[0].id == claimed_attempt_id
            assert attempts[0].attempt_number == 1
            assert attempts[0].binding_revision == 2
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_claim_uses_post_wait_db_clock_for_lease_expiration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.services.lineage_invalidation_execution_claim_service."
        "EXECUTION_RETRY_COOLDOWN",
        timedelta(seconds=0),
    )
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

        organization_id = organization.id
        intent_id = intent.id

        seed_now = setup_db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        first = LineageInvalidationExecutionAttemptRecord(
            organization_id=organization_id,
            intent_id=intent_id,
            attempt_number=1,
            binding_revision=1,
            claimed_at=seed_now - timedelta(seconds=300),
            lease_expires_at=seed_now + timedelta(seconds=1),
        )
        setup_db.add(first)
        setup_db.commit()

        first_id = first.id

        writer_has_lock = threading.Event()
        allow_writer_commit = threading.Event()
        claim_started = threading.Event()
        claim_finished = threading.Event()

        lock = threading.Lock()
        claimed_attempts: list[tuple[uuid.UUID, int]] = []
        errors: list[BaseException] = []

        def hold_policy_authority() -> None:
            db = SessionLocal()

            try:
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

        def claim() -> None:
            db = SessionLocal()

            try:
                if not writer_has_lock.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out waiting for policy authority lock."
                    )

                claim_started.set()

                result = claim_lineage_invalidation_execution(
                    db,
                    organization_id=organization_id,
                    intent_id=intent_id,
                    lease_seconds=300,
                )

                attempt_id = result.record.id
                attempt_number = result.record.attempt_number
                db.commit()

                with lock:
                    claimed_attempts.append(
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
                claim_finished.set()
                db.close()

        writer_thread = threading.Thread(target=hold_policy_authority)
        claim_thread = threading.Thread(target=claim)

        writer_thread.start()

        assert writer_has_lock.wait(timeout=10)

        claim_thread.start()

        assert claim_started.wait(timeout=10)
        assert not claim_finished.wait(timeout=0.25)

        while True:
            current_db_time = setup_db.execute(
                select(func.clock_timestamp())
            ).scalar_one()

            if current_db_time >= first.lease_expires_at:
                break

            time.sleep(0.05)

        allow_writer_commit.set()

        writer_thread.join(timeout=10)
        claim_thread.join(timeout=10)

        assert not writer_thread.is_alive()
        assert not claim_thread.is_alive()
        assert errors == []
        assert len(claimed_attempts) == 1

        claimed_attempt_id, attempt_number = claimed_attempts[0]

        assert claimed_attempt_id != first_id
        assert attempt_number == 2

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

            assert len(attempts) == 2
            assert attempts[0].id == first_id
            assert attempts[0].attempt_number == 1
            assert attempts[1].id == claimed_attempt_id
            assert attempts[1].attempt_number == 2
            assert attempts[1].claimed_at >= attempts[0].lease_expires_at
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_claim_defers_retry_during_cooldown() -> None:
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
            claimed_at=db_now - timedelta(seconds=330),
            lease_expires_at=db_now - timedelta(seconds=30),
        )

        db.add(first)
        db.commit()

        first_id = first.id
        expected_retry_after = first.lease_expires_at + timedelta(
            seconds=60
        )

        with pytest.raises(
            LineageInvalidationExecutionRetryDeferredError
        ) as exc_info:
            claim_lineage_invalidation_execution(
                db,
                organization_id=organization.id,
                intent_id=intent.id,
            )

        assert exc_info.value.current_attempt_id == first_id
        assert exc_info.value.retry_after == expected_retry_after

        db.rollback()

        attempts = (
            db.query(LineageInvalidationExecutionAttemptRecord)
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == intent.id,
            )
            .all()
        )

        assert len(attempts) == 1
        assert attempts[0].id == first_id

    finally:
        db.close()
        _cleanup(organization_ids)


def test_claim_allows_retry_after_cooldown() -> None:
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
            claimed_at=db_now - timedelta(seconds=390),
            lease_expires_at=db_now - timedelta(seconds=90),
        )

        db.add(first)
        db.commit()

        first_id = first.id

        second = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )

        assert second.record.id != first_id
        assert second.record.attempt_number == 2
        assert second.record.binding_revision == 1

        db.commit()

        attempts = (
            db.query(LineageInvalidationExecutionAttemptRecord)
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == intent.id,
            )
            .order_by(
                LineageInvalidationExecutionAttemptRecord.attempt_number.asc()
            )
            .all()
        )

        assert len(attempts) == 2
        assert attempts[0].id == first_id
        assert attempts[1].id == second.record.id

    finally:
        db.close()
        _cleanup(organization_ids)



def test_claim_rejects_revision_outside_causal_path_without_attempt() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
        )
        persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=LineageExecutionTargetBinding(
                identity=LineageExecutionTargetIdentity(
                    source_external_id="source-a",
                    asset_external_id="index-a",
                ),
                binding_revision=2,
                provider="pgvector",
                target_kind="langchain_pgvector_document_revision",
                configuration={
                    "collection_name": "rag-index",
                    "document_id": "unrelated-document",
                    "document_revision_external_id": "unrelated-r1",
                },
            ),
        )
        db.commit()

        with pytest.raises(LineageInvalidationExecutionUnsafeTargetError):
            claim_lineage_invalidation_execution(
                db,
                organization_id=organization.id,
                intent_id=intent.id,
            )
        db.rollback()
        assert _attempt_count(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)
