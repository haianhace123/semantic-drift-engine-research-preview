from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.domain.lineage_invalidation import (
    LineageInvalidationDecision,
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.models.organization import Organization
from src.services.lineage_invalidation_eligibility_service import (
    evaluate_lineage_invalidation_eligibility,
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
        name=f"lineage-eligibility-{suffix}",
        slug=f"lineage-eligibility-{suffix}",
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


def _observation(
    *,
    revision_external_id: str,
    sequence: int,
    observation_kind: str = "materialization",
) -> LineageObservation:
    dependencies: tuple[LineageRevisionReference, ...] = ()

    if observation_kind == "consumption":
        dependencies = (
            LineageRevisionReference(
                asset_external_id="chunk-a",
                revision_external_id="chunk-a-r3",
                dependency_type="retrieves_from",
            ),
        )

    return LineageObservation(
        observation_kind=observation_kind,
        source_external_id="source-a",
        snapshot_external_id=f"snapshot-{sequence}",
        asset_external_id="index-a",
        asset_kind="index",
        revision_external_id=revision_external_id,
        sequence=sequence,
        dependencies=dependencies,
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
        observation=_observation(
            revision_external_id=revision_external_id,
            sequence=sequence,
        ),
    )


def _establish_eligible_authority(
    db,
    *,
    organization_id: uuid.UUID,
) -> None:
    activate_lineage_invalidation_policy(
        db,
        organization_id=organization_id,
        policy=_policy(7),
    )
    _persist_materialization(
        db,
        organization_id=organization_id,
    )


def _delete_organization(
    organization_id: uuid.UUID,
) -> None:
    db = SessionLocal()

    try:
        db.execute(
            delete(Organization).where(
                Organization.id == organization_id
            )
        )
        db.commit()
    finally:
        db.close()


def test_executable_decision_is_eligible_under_exact_authority():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        _establish_eligible_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        eligibility = evaluate_lineage_invalidation_eligibility(
            db,
            organization_id=organization.id,
            decision=_decision(),
        )

        assert eligibility.eligible is True
        assert eligibility.reason_code == "eligible"

    finally:
        db.close()
        if organization_id is not None:
            _delete_organization(organization_id)


@pytest.mark.parametrize(
    "disposition",
    (
        "invalidate",
        "rematerialize",
    ),
)
def test_supported_executable_dispositions_are_eligible(
    disposition,
):
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        _establish_eligible_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        eligibility = evaluate_lineage_invalidation_eligibility(
            db,
            organization_id=organization.id,
            decision=_decision(
                disposition=disposition,
            ),
        )

        assert eligibility.eligible is True
        assert eligibility.reason_code == "eligible"

    finally:
        db.close()
        if organization_id is not None:
            _delete_organization(organization_id)


def test_no_action_is_ineligible_without_authority():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id
        db.commit()

        eligibility = evaluate_lineage_invalidation_eligibility(
            db,
            organization_id=organization.id,
            decision=_decision(
                disposition="no_action",
            ),
        )

        assert eligibility.eligible is False
        assert eligibility.reason_code == "no_action"

    finally:
        db.close()
        if organization_id is not None:
            _delete_organization(organization_id)


def test_decision_is_ineligible_when_policy_is_not_active():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        _persist_materialization(
            db,
            organization_id=organization.id,
        )
        db.commit()

        eligibility = evaluate_lineage_invalidation_eligibility(
            db,
            organization_id=organization.id,
            decision=_decision(),
        )

        assert eligibility.eligible is False
        assert eligibility.reason_code == "policy_not_active"

    finally:
        db.close()
        if organization_id is not None:
            _delete_organization(organization_id)


def test_decision_is_ineligible_when_policy_revision_is_superseded():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(8),
        )
        _persist_materialization(
            db,
            organization_id=organization.id,
        )
        db.commit()

        eligibility = evaluate_lineage_invalidation_eligibility(
            db,
            organization_id=organization.id,
            decision=_decision(
                policy_revision=7,
            ),
        )

        assert eligibility.eligible is False
        assert (
            eligibility.reason_code
            == "policy_revision_superseded"
        )

    finally:
        db.close()
        if organization_id is not None:
            _delete_organization(organization_id)


def test_decision_is_ineligible_when_policy_revision_is_not_yet_active():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(7),
        )
        _persist_materialization(
            db,
            organization_id=organization.id,
        )
        db.commit()

        eligibility = evaluate_lineage_invalidation_eligibility(
            db,
            organization_id=organization.id,
            decision=_decision(
                policy_revision=8,
            ),
        )

        assert eligibility.eligible is False
        assert (
            eligibility.reason_code
            == "policy_revision_not_active"
        )

    finally:
        db.close()
        if organization_id is not None:
            _delete_organization(organization_id)


def test_decision_is_ineligible_without_target_materialization_authority():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(7),
        )
        db.commit()

        eligibility = evaluate_lineage_invalidation_eligibility(
            db,
            organization_id=organization.id,
            decision=_decision(),
        )

        assert eligibility.eligible is False
        assert (
            eligibility.reason_code
            == "target_authority_missing"
        )

    finally:
        db.close()
        if organization_id is not None:
            _delete_organization(organization_id)


def test_historical_target_revision_is_ineligible():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(7),
        )
        _persist_materialization(
            db,
            organization_id=organization.id,
            revision_external_id="index-a-r8",
            sequence=8,
        )
        db.commit()

        eligibility = evaluate_lineage_invalidation_eligibility(
            db,
            organization_id=organization.id,
            decision=_decision(
                target_revision="index-a-r7",
            ),
        )

        assert eligibility.eligible is False
        assert eligibility.reason_code == "target_not_current"

    finally:
        db.close()
        if organization_id is not None:
            _delete_organization(organization_id)


def test_consumption_does_not_substitute_for_materialization_authority():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(7),
        )
        persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_observation(
                revision_external_id="index-a-r7",
                sequence=7,
                observation_kind="consumption",
            ),
        )
        db.commit()

        eligibility = evaluate_lineage_invalidation_eligibility(
            db,
            organization_id=organization.id,
            decision=_decision(),
        )

        assert eligibility.eligible is False
        assert (
            eligibility.reason_code
            == "target_authority_missing"
        )

    finally:
        db.close()
        if organization_id is not None:
            _delete_organization(organization_id)


def test_eligibility_is_tenant_isolated():
    db = SessionLocal()
    first_organization_id = None
    second_organization_id = None

    try:
        first_organization = _make_organization(db)
        second_organization = _make_organization(db)

        first_organization_id = first_organization.id
        second_organization_id = second_organization.id

        _establish_eligible_authority(
            db,
            organization_id=first_organization.id,
        )
        db.commit()

        eligibility = evaluate_lineage_invalidation_eligibility(
            db,
            organization_id=second_organization.id,
            decision=_decision(),
        )

        assert eligibility.eligible is False
        assert eligibility.reason_code == "policy_not_active"

    finally:
        db.close()
        if first_organization_id is not None:
            _delete_organization(first_organization_id)
        if second_organization_id is not None:
            _delete_organization(second_organization_id)


def test_policy_activation_change_invalidates_existing_decision():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        _establish_eligible_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        decision = _decision()

        before = evaluate_lineage_invalidation_eligibility(
            db,
            organization_id=organization.id,
            decision=decision,
        )

        activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(8),
        )
        db.commit()

        after = evaluate_lineage_invalidation_eligibility(
            db,
            organization_id=organization.id,
            decision=decision,
        )

        assert before.eligible is True
        assert before.reason_code == "eligible"
        assert after.eligible is False
        assert (
            after.reason_code
            == "policy_revision_superseded"
        )

    finally:
        db.close()
        if organization_id is not None:
            _delete_organization(organization_id)


def test_materialization_advance_invalidates_existing_decision():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        _establish_eligible_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        decision = _decision()

        before = evaluate_lineage_invalidation_eligibility(
            db,
            organization_id=organization.id,
            decision=decision,
        )

        _persist_materialization(
            db,
            organization_id=organization.id,
            revision_external_id="index-a-r8",
            sequence=8,
        )
        db.commit()

        after = evaluate_lineage_invalidation_eligibility(
            db,
            organization_id=organization.id,
            decision=decision,
        )

        assert before.eligible is True
        assert before.reason_code == "eligible"
        assert after.eligible is False
        assert after.reason_code == "target_not_current"

    finally:
        db.close()
        if organization_id is not None:
            _delete_organization(organization_id)


def test_eligibility_evaluation_does_not_mutate_authority_state():
    setup_db = SessionLocal()
    evaluation_db = SessionLocal()
    verify_db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(setup_db)
        organization_id = organization.id

        _establish_eligible_authority(
            setup_db,
            organization_id=organization.id,
        )
        setup_db.commit()

        eligibility = evaluate_lineage_invalidation_eligibility(
            evaluation_db,
            organization_id=organization.id,
            decision=_decision(),
        )

        assert eligibility.eligible is True

        evaluation_db.rollback()

        persisted = evaluate_lineage_invalidation_eligibility(
            verify_db,
            organization_id=organization.id,
            decision=_decision(),
        )

        assert persisted.eligible is True
        assert persisted.reason_code == "eligible"

    finally:
        setup_db.close()
        evaluation_db.close()
        verify_db.close()

        if organization_id is not None:
            _delete_organization(organization_id)