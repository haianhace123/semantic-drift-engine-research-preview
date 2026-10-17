from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.domain.lineage_invalidation import (
    LineageInvalidationPolicyIdentity,
)
from src.models.lineage_invalidation_policy_activation import (
    LineageInvalidationPolicyActivationRecord,
)
from src.models.organization import Organization
from src.services.lineage_invalidation_policy_activation_service import (
    LineageInvalidationPolicyStaleActivationError,
    activate_lineage_invalidation_policy,
    load_active_lineage_invalidation_policy,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"lineage-policy-activation-{suffix}",
        slug=f"lineage-policy-activation-{suffix}",
        billing_email=f"{suffix}@example.com",
    )

    db.add(organization)
    db.flush()

    return organization


def _policy(
    revision: int,
    *,
    policy_external_id: str = "rag-invalidation",
) -> LineageInvalidationPolicyIdentity:
    return LineageInvalidationPolicyIdentity(
        policy_external_id=policy_external_id,
        revision=revision,
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
            delete(
                LineageInvalidationPolicyActivationRecord
            ).where(
                LineageInvalidationPolicyActivationRecord.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(
                Organization
            ).where(
                Organization.id.in_(
                    organization_ids
                )
            )
        )

        db.commit()

    finally:
        db.close()


def test_activation_is_absent_until_explicitly_activated():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        state = load_active_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy_external_id="rag-invalidation",
        )

        assert state is None

    finally:
        db.close()
        _cleanup(organization_ids)


def test_activation_selects_exact_policy_revision():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        result = activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(7),
        )

        assert result.outcome == "activated"
        assert result.state.organization_id == organization.id
        assert result.state.policy == _policy(7)
        assert result.state.activated_at is not None

        db.commit()

        persisted = load_active_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy_external_id="rag-invalidation",
        )

        assert persisted is not None
        assert persisted.policy == _policy(7)
        assert persisted.activated_at == result.state.activated_at

    finally:
        db.close()
        _cleanup(organization_ids)


def test_activation_replay_is_idempotent_and_preserves_activated_at():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        first = activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(7),
        )
        second = activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(7),
        )

        assert first.outcome == "activated"
        assert second.outcome == "replayed"
        assert second.state.policy == _policy(7)
        assert second.state.activated_at == first.state.activated_at

        db.commit()

    finally:
        db.close()
        _cleanup(organization_ids)


def test_activation_advances_monotonically():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        first = activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(7),
        )
        second = activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(8),
        )

        assert first.state.policy == _policy(7)
        assert second.outcome == "activated"
        assert second.state.policy == _policy(8)

        db.commit()

        persisted = load_active_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy_external_id="rag-invalidation",
        )

        assert persisted is not None
        assert persisted.policy == _policy(8)

    finally:
        db.close()
        _cleanup(organization_ids)


def test_activation_rejects_stale_revision():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(8),
        )

        with pytest.raises(
            LineageInvalidationPolicyStaleActivationError
        ):
            activate_lineage_invalidation_policy(
                db,
                organization_id=organization.id,
                policy=_policy(7),
            )

        state = load_active_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy_external_id="rag-invalidation",
        )

        assert state is not None
        assert state.policy == _policy(8)

        db.rollback()

    finally:
        db.close()
        _cleanup(organization_ids)


def test_stale_activation_preserves_caller_transaction():
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        activate_lineage_invalidation_policy(
            setup_db,
            organization_id=organization.id,
            policy=_policy(8),
        )
        setup_db.commit()

        second_policy = _policy(
            3,
            policy_external_id="agent-invalidation",
        )

        activate_lineage_invalidation_policy(
            execution_db,
            organization_id=organization.id,
            policy=second_policy,
        )

        with pytest.raises(
            LineageInvalidationPolicyStaleActivationError
        ):
            activate_lineage_invalidation_policy(
                execution_db,
                organization_id=organization.id,
                policy=_policy(7),
            )

        execution_db.commit()

        first_persisted = load_active_lineage_invalidation_policy(
            verify_db,
            organization_id=organization.id,
            policy_external_id="rag-invalidation",
        )
        second_persisted = load_active_lineage_invalidation_policy(
            verify_db,
            organization_id=organization.id,
            policy_external_id="agent-invalidation",
        )

        assert first_persisted is not None
        assert first_persisted.policy == _policy(8)

        assert second_persisted is not None
        assert second_persisted.policy == second_policy

    finally:
        setup_db.close()
        execution_db.close()
        verify_db.close()
        _cleanup(organization_ids)


def test_activation_is_scoped_by_policy_external_id():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(
                7,
                policy_external_id="rag-invalidation",
            ),
        )
        activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(
                3,
                policy_external_id="agent-invalidation",
            ),
        )

        rag = load_active_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy_external_id="rag-invalidation",
        )
        agent = load_active_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy_external_id="agent-invalidation",
        )

        assert rag is not None
        assert agent is not None
        assert rag.policy.revision == 7
        assert agent.policy.revision == 3

        db.commit()

    finally:
        db.close()
        _cleanup(organization_ids)


def test_activation_is_tenant_isolated():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        first_organization = _make_organization(db)
        second_organization = _make_organization(db)

        organization_ids.extend(
            [
                first_organization.id,
                second_organization.id,
            ]
        )

        db.commit()

        activate_lineage_invalidation_policy(
            db,
            organization_id=first_organization.id,
            policy=_policy(7),
        )

        second_state = load_active_lineage_invalidation_policy(
            db,
            organization_id=second_organization.id,
            policy_external_id="rag-invalidation",
        )

        assert second_state is None

        db.commit()

    finally:
        db.close()
        _cleanup(organization_ids)


def test_activation_rolls_back_with_caller_transaction():
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        activate_lineage_invalidation_policy(
            execution_db,
            organization_id=organization.id,
            policy=_policy(7),
        )

        execution_db.rollback()

        persisted = load_active_lineage_invalidation_policy(
            verify_db,
            organization_id=organization.id,
            policy_external_id="rag-invalidation",
        )

        assert persisted is None

    finally:
        setup_db.close()
        execution_db.close()
        verify_db.close()
        _cleanup(organization_ids)


@dataclass(frozen=True)
class _ConcurrentActivationResult:
    revision: int
    outcome: str | None
    stale: bool
    error: BaseException | None


def _run_concurrent_activation(
    *,
    organization_id: uuid.UUID,
    revision: int,
    barrier: threading.Barrier,
    results: list[_ConcurrentActivationResult],
    lock: threading.Lock,
) -> None:
    db = SessionLocal()

    try:
        barrier.wait()

        try:
            result = activate_lineage_invalidation_policy(
                db,
                organization_id=organization_id,
                policy=_policy(revision),
            )
            db.commit()

            observed = _ConcurrentActivationResult(
                revision=revision,
                outcome=result.outcome,
                stale=False,
                error=None,
            )

        except LineageInvalidationPolicyStaleActivationError:
            db.rollback()

            observed = _ConcurrentActivationResult(
                revision=revision,
                outcome=None,
                stale=True,
                error=None,
            )

        except BaseException as exc:
            db.rollback()

            observed = _ConcurrentActivationResult(
                revision=revision,
                outcome=None,
                stale=False,
                error=exc,
            )

        with lock:
            results.append(observed)

    finally:
        db.close()


def test_concurrent_same_revision_converges_idempotently():
    setup_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        barrier = threading.Barrier(2)
        lock = threading.Lock()
        results: list[_ConcurrentActivationResult] = []

        threads = [
            threading.Thread(
                target=_run_concurrent_activation,
                kwargs={
                    "organization_id": organization.id,
                    "revision": 7,
                    "barrier": barrier,
                    "results": results,
                    "lock": lock,
                },
            )
            for _ in range(2)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()

        assert len(results) == 2
        assert all(result.error is None for result in results)
        assert all(result.stale is False for result in results)
        assert sorted(
            result.outcome
            for result in results
        ) == [
            "activated",
            "replayed",
        ]

        persisted = load_active_lineage_invalidation_policy(
            verify_db,
            organization_id=organization.id,
            policy_external_id="rag-invalidation",
        )

        assert persisted is not None
        assert persisted.policy == _policy(7)

    finally:
        setup_db.close()
        verify_db.close()
        _cleanup(organization_ids)


def test_concurrent_different_revisions_converge_to_highest_revision():
    setup_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        barrier = threading.Barrier(2)
        lock = threading.Lock()
        results: list[_ConcurrentActivationResult] = []

        threads = [
            threading.Thread(
                target=_run_concurrent_activation,
                kwargs={
                    "organization_id": organization.id,
                    "revision": revision,
                    "barrier": barrier,
                    "results": results,
                    "lock": lock,
                },
            )
            for revision in (7, 8)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()

        assert len(results) == 2
        assert all(result.error is None for result in results)

        by_revision = {
            result.revision: result
            for result in results
        }

        assert by_revision[8].outcome == "activated"
        assert by_revision[8].stale is False

        assert (
            (
                by_revision[7].outcome == "activated"
                and by_revision[7].stale is False
            )
            or (
                by_revision[7].outcome is None
                and by_revision[7].stale is True
            )
        )

        persisted = load_active_lineage_invalidation_policy(
            verify_db,
            organization_id=organization.id,
            policy_external_id="rag-invalidation",
        )

        assert persisted is not None
        assert persisted.policy == _policy(8)

    finally:
        setup_db.close()
        verify_db.close()
        _cleanup(organization_ids)