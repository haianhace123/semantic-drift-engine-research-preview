from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import delete, func

from src.core.database import SessionLocal
from src.domain.lineage_invalidation import (
    LineageInvalidationDecision,
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_observation import LineageObservation
from src.domain.lineage_revision import LineageRevisionIdentity
from src.models.lineage_invalidation_decision import (
    LineageInvalidationDecisionRecord,
)
from src.models.lineage_invalidation_execution_intent import (
    LineageInvalidationExecutionIntentRecord,
)
from src.models.lineage_invalidation_policy_activation import (
    LineageInvalidationPolicyActivationRecord,
)
from src.models.lineage_observation import LineageObservationRecord
from src.models.organization import Organization
from src.services.lineage_invalidation_execution_intent_service import (
    LineageInvalidationDecisionNotFoundError,
    LineageInvalidationExecutionNotEligibleError,
    authorize_lineage_invalidation_execution,
)
from src.services.lineage_invalidation_persistence_service import (
    persist_lineage_invalidation_decision,
)
from src.services.lineage_invalidation_policy_activation_service import (
    activate_lineage_invalidation_policy,
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
        name=f"lineage-execution-intent-{suffix}",
        slug=f"lineage-execution-intent-{suffix}",
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
    disposition: str = "invalidate",
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
        disposition=disposition,
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
        dependencies=(),
    )


def _persist_decision(
    db,
    *,
    organization_id: uuid.UUID,
    decision: LineageInvalidationDecision | None = None,
):
    return persist_lineage_invalidation_decision(
        db,
        organization_id=organization_id,
        decision=decision or _decision(),
    )


def _activate_policy(
    db,
    *,
    organization_id: uuid.UUID,
    revision: int = 7,
) -> None:
    activate_lineage_invalidation_policy(
        db,
        organization_id=organization_id,
        policy=_policy(revision),
    )


def _persist_materialization(
    db,
    *,
    organization_id: uuid.UUID,
    revision_external_id: str = "index-a-r7",
    sequence: int = 7,
) -> None:
    persist_lineage_observation(
        db,
        organization_id=organization_id,
        observation=_materialization(
            revision_external_id=revision_external_id,
            sequence=sequence,
        ),
    )


def _establish_eligible_authority(
    db,
    *,
    organization_id: uuid.UUID,
) -> None:
    _activate_policy(
        db,
        organization_id=organization_id,
    )
    _persist_materialization(
        db,
        organization_id=organization_id,
    )


def _intent_count(
    db,
    *,
    organization_id: uuid.UUID,
) -> int:
    return (
        db.query(func.count(LineageInvalidationExecutionIntentRecord.id))
        .filter(
            LineageInvalidationExecutionIntentRecord.organization_id
            == organization_id
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
            delete(Organization).where(
                Organization.id.in_(organization_ids)
            )
        )
        db.commit()
    finally:
        db.close()


@pytest.mark.parametrize(
    "disposition",
    (
        "invalidate",
        "rematerialize",
    ),
)
def test_eligible_decision_creates_execution_intent(
    disposition: str,
) -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            db,
            organization_id=organization.id,
            decision=_decision(disposition=disposition),
        )
        _establish_eligible_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        result = authorize_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )

        assert result.created is True
        assert result.record.id is not None
        assert result.record.organization_id == organization.id
        assert result.record.decision_id == persisted.record.id
        assert result.record.authorized_at is not None
        assert _intent_count(
            db,
            organization_id=organization.id,
        ) == 1
    finally:
        db.close()
        _cleanup(organization_ids)


def test_identical_authorization_replays_existing_intent() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        _establish_eligible_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        first = authorize_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )
        db.commit()

        second = authorize_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )

        assert first.created is True
        assert second.created is False
        assert second.record.id == first.record.id
        assert second.record.decision_id == persisted.record.id
        assert _intent_count(
            db,
            organization_id=organization.id,
        ) == 1
    finally:
        db.close()
        _cleanup(organization_ids)


@pytest.mark.parametrize(
    (
        "decision",
        "active_policy_revision",
        "materialization_revision",
        "materialization_sequence",
        "expected_reason",
    ),
    (
        (
            _decision(disposition="no_action"),
            7,
            "index-a-r7",
            7,
            "no_action",
        ),
        (
            _decision(),
            None,
            "index-a-r7",
            7,
            "policy_not_active",
        ),
        (
            _decision(policy_revision=8),
            7,
            "index-a-r7",
            7,
            "policy_revision_not_active",
        ),
        (
            _decision(policy_revision=7),
            8,
            "index-a-r7",
            7,
            "policy_revision_superseded",
        ),
        (
            _decision(),
            7,
            None,
            None,
            "target_authority_missing",
        ),
        (
            _decision(),
            7,
            "index-a-r8",
            8,
            "target_not_current",
        ),
    ),
)
def test_ineligible_decision_does_not_create_execution_intent(
    decision: LineageInvalidationDecision,
    active_policy_revision: int | None,
    materialization_revision: str | None,
    materialization_sequence: int | None,
    expected_reason: str,
) -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            db,
            organization_id=organization.id,
            decision=decision,
        )

        if active_policy_revision is not None:
            _activate_policy(
                db,
                organization_id=organization.id,
                revision=active_policy_revision,
            )

        if materialization_revision is not None:
            assert materialization_sequence is not None
            _persist_materialization(
                db,
                organization_id=organization.id,
                revision_external_id=materialization_revision,
                sequence=materialization_sequence,
            )

        db.commit()

        with pytest.raises(
            LineageInvalidationExecutionNotEligibleError
        ) as exc_info:
            authorize_lineage_invalidation_execution(
                db,
                organization_id=organization.id,
                decision_record_id=persisted.record.id,
            )

        assert exc_info.value.reason_code == expected_reason
        assert _intent_count(
            db,
            organization_id=organization.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_unknown_decision_is_rejected_without_intent() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        with pytest.raises(
            LineageInvalidationDecisionNotFoundError
        ):
            authorize_lineage_invalidation_execution(
                db,
                organization_id=organization.id,
                decision_record_id=uuid.uuid4(),
            )

        assert _intent_count(
            db,
            organization_id=organization.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_decision_lookup_is_tenant_scoped() -> None:
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

        persisted = _persist_decision(
            db,
            organization_id=owner.id,
        )
        _establish_eligible_authority(
            db,
            organization_id=owner.id,
        )
        db.commit()

        with pytest.raises(
            LineageInvalidationDecisionNotFoundError
        ):
            authorize_lineage_invalidation_execution(
                db,
                organization_id=other.id,
                decision_record_id=persisted.record.id,
            )

        assert _intent_count(
            db,
            organization_id=owner.id,
        ) == 0
        assert _intent_count(
            db,
            organization_id=other.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_caller_rollback_removes_created_intent() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        _establish_eligible_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        result = authorize_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )

        assert result.created is True
        assert _intent_count(
            db,
            organization_id=organization.id,
        ) == 1

        db.rollback()

        assert _intent_count(
            db,
            organization_id=organization.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_authorization_does_not_commit_caller_transaction() -> None:
    db = SessionLocal()
    observer = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        _establish_eligible_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        result = authorize_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )

        assert result.created is True

        observer_count = _intent_count(
            observer,
            organization_id=organization.id,
        )
        assert observer_count == 0

        db.commit()
        observer.rollback()

        observer_count = _intent_count(
            observer,
            organization_id=organization.id,
        )
        assert observer_count == 1
    finally:
        observer.close()
        db.close()
        _cleanup(organization_ids)


def test_ineligible_authorization_preserves_caller_transaction() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        db.commit()

        marker = Organization(
            id=uuid.uuid4(),
            name=f"lineage-intent-marker-{uuid.uuid4().hex[:12]}",
            slug=f"lineage-intent-marker-{uuid.uuid4().hex[:12]}",
            billing_email=f"{uuid.uuid4().hex[:12]}@example.com",
        )
        organization_ids.append(marker.id)
        db.add(marker)
        db.flush()

        with pytest.raises(
            LineageInvalidationExecutionNotEligibleError
        ) as exc_info:
            authorize_lineage_invalidation_execution(
                db,
                organization_id=organization.id,
                decision_record_id=persisted.record.id,
            )

        assert exc_info.value.reason_code == "policy_not_active"

        db.commit()

        persisted_marker = (
            db.query(Organization)
            .filter(
                Organization.id == marker.id
            )
            .one_or_none()
        )
        assert persisted_marker is not None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_concurrent_identical_authorization_creates_one_intent() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            setup_db,
            organization_id=organization.id,
        )
        _establish_eligible_authority(
            setup_db,
            organization_id=organization.id,
        )
        setup_db.commit()

        organization_id = organization.id
        decision_record_id = persisted.record.id

        barrier = threading.Barrier(2)
        lock = threading.Lock()
        results: list[tuple[bool, uuid.UUID]] = []
        errors: list[BaseException] = []

        def authorize() -> None:
            db = SessionLocal()

            try:
                barrier.wait()

                result = authorize_lineage_invalidation_execution(
                    db,
                    organization_id=organization_id,
                    decision_record_id=decision_record_id,
                )
                db.commit()

                with lock:
                    results.append(
                        (
                            result.created,
                            result.record.id,
                        )
                    )
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                db.close()

        threads = [
            threading.Thread(target=authorize),
            threading.Thread(target=authorize),
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
        assert len(results) == 2
        assert sorted(
            created
            for created, _ in results
        ) == [
            False,
            True,
        ]
        assert len(
            {
                record_id
                for _, record_id in results
            }
        ) == 1

        verify_db = SessionLocal()

        try:
            assert _intent_count(
                verify_db,
                organization_id=organization_id,
            ) == 1
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_policy_advance_and_authorization_are_linearizable() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            setup_db,
            organization_id=organization.id,
        )
        _establish_eligible_authority(
            setup_db,
            organization_id=organization.id,
        )
        setup_db.commit()

        organization_id = organization.id
        decision_record_id = persisted.record.id

        barrier = threading.Barrier(2)
        lock = threading.Lock()
        authorization_results: list[bool] = []
        authorization_reasons: list[str] = []
        errors: list[BaseException] = []

        def authorize() -> None:
            db = SessionLocal()

            try:
                barrier.wait()

                try:
                    result = authorize_lineage_invalidation_execution(
                        db,
                        organization_id=organization_id,
                        decision_record_id=decision_record_id,
                    )
                    db.commit()

                    with lock:
                        authorization_results.append(result.created)
                except LineageInvalidationExecutionNotEligibleError as exc:
                    db.rollback()

                    with lock:
                        authorization_reasons.append(exc.reason_code)
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
            threading.Thread(target=authorize),
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
                authorization_results == [True]
                and authorization_reasons == []
            )
            or (
                authorization_results == []
                and authorization_reasons
                == ["policy_revision_superseded"]
            )
        )

        verify_db = SessionLocal()

        try:
            intent_count = _intent_count(
                verify_db,
                organization_id=organization_id,
            )

            if authorization_results:
                assert intent_count == 1
            else:
                assert intent_count == 0
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_materialization_advance_and_authorization_are_linearizable() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            setup_db,
            organization_id=organization.id,
        )
        _establish_eligible_authority(
            setup_db,
            organization_id=organization.id,
        )
        setup_db.commit()

        organization_id = organization.id
        decision_record_id = persisted.record.id

        barrier = threading.Barrier(2)
        lock = threading.Lock()
        authorization_results: list[bool] = []
        authorization_reasons: list[str] = []
        errors: list[BaseException] = []

        def authorize() -> None:
            db = SessionLocal()

            try:
                barrier.wait()

                try:
                    result = authorize_lineage_invalidation_execution(
                        db,
                        organization_id=organization_id,
                        decision_record_id=decision_record_id,
                    )
                    db.commit()

                    with lock:
                        authorization_results.append(result.created)
                except LineageInvalidationExecutionNotEligibleError as exc:
                    db.rollback()

                    with lock:
                        authorization_reasons.append(exc.reason_code)
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

                _persist_materialization(
                    db,
                    organization_id=organization_id,
                    revision_external_id="index-a-r8",
                    sequence=8,
                )
                db.commit()
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                db.close()

        threads = [
            threading.Thread(target=authorize),
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
                authorization_results == [True]
                and authorization_reasons == []
            )
            or (
                authorization_results == []
                and authorization_reasons == ["target_not_current"]
            )
        )

        verify_db = SessionLocal()

        try:
            intent_count = _intent_count(
                verify_db,
                organization_id=organization_id,
            )

            if authorization_results:
                assert intent_count == 1
            else:
                assert intent_count == 0
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)
