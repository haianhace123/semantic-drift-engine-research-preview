from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.models.organization import Organization
from src.models.organization_alert_policy_revision import (
    OrganizationAlertPolicyRevision,
)
from src.services.alert_policy_revision_service import (
    AlertPolicyRevisionConflictError,
    bump_alert_policy_revision,
    load_alert_policy_revision,
    mark_alert_policy_reconciled,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"alert-policy-revision-{suffix}",
        slug=f"alert-policy-revision-{suffix}",
        billing_email=f"{suffix}@example.com",
    )

    db.add(organization)
    db.flush()

    return organization


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
                OrganizationAlertPolicyRevision
            ).where(
                OrganizationAlertPolicyRevision.organization_id.in_(
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


def test_alert_policy_revision_is_absent_until_first_mutation():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        db.commit()

        state = load_alert_policy_revision(
            db,
            organization_id=organization.id,
        )

        assert state is None

    finally:
        db.close()
        _cleanup(
            organization_ids
        )


def test_alert_policy_revision_bump_is_monotonic():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        db.commit()

        first = bump_alert_policy_revision(
            db,
            organization_id=organization.id,
        )

        assert first.organization_id == organization.id
        assert first.policy_revision == 1
        assert first.reconciled_revision == 0
        assert first.reconciliation_required is True

        second = bump_alert_policy_revision(
            db,
            organization_id=organization.id,
        )

        assert second.organization_id == organization.id
        assert second.policy_revision == 2
        assert second.reconciled_revision == 0
        assert second.reconciliation_required is True

        db.commit()

        persisted = load_alert_policy_revision(
            db,
            organization_id=organization.id,
        )

        assert persisted is not None
        assert persisted.policy_revision == 2
        assert persisted.reconciled_revision == 0

    finally:
        db.close()
        _cleanup(
            organization_ids
        )


def test_alert_policy_revision_can_be_marked_reconciled():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        db.commit()

        first = bump_alert_policy_revision(
            db,
            organization_id=organization.id,
        )
        second = bump_alert_policy_revision(
            db,
            organization_id=organization.id,
        )

        assert first.policy_revision == 1
        assert second.policy_revision == 2

        reconciled = mark_alert_policy_reconciled(
            db,
            organization_id=organization.id,
            policy_revision=second.policy_revision,
        )

        assert reconciled.policy_revision == 2
        assert reconciled.reconciled_revision == 2
        assert reconciled.reconciliation_required is False
        assert reconciled.reconciled_at is not None

        db.commit()

        persisted = load_alert_policy_revision(
            db,
            organization_id=organization.id,
        )

        assert persisted is not None
        assert persisted.policy_revision == 2
        assert persisted.reconciled_revision == 2
        assert persisted.reconciliation_required is False

    finally:
        db.close()
        _cleanup(
            organization_ids
        )


def test_alert_policy_reconciliation_acknowledgement_is_idempotent():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        db.commit()

        revision = bump_alert_policy_revision(
            db,
            organization_id=organization.id,
        )

        first = mark_alert_policy_reconciled(
            db,
            organization_id=organization.id,
            policy_revision=revision.policy_revision,
        )

        second = mark_alert_policy_reconciled(
            db,
            organization_id=organization.id,
            policy_revision=revision.policy_revision,
        )

        assert first.policy_revision == 1
        assert first.reconciled_revision == 1
        assert second.policy_revision == 1
        assert second.reconciled_revision == 1
        assert second.reconciliation_required is False

        db.commit()

    finally:
        db.close()
        _cleanup(
            organization_ids
        )


def test_alert_policy_reconciliation_rejects_stale_revision():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        db.commit()

        first = bump_alert_policy_revision(
            db,
            organization_id=organization.id,
        )

        second = bump_alert_policy_revision(
            db,
            organization_id=organization.id,
        )

        assert first.policy_revision == 1
        assert second.policy_revision == 2

        with pytest.raises(
            AlertPolicyRevisionConflictError
        ):
            mark_alert_policy_reconciled(
                db,
                organization_id=organization.id,
                policy_revision=first.policy_revision,
            )

        state = load_alert_policy_revision(
            db,
            organization_id=organization.id,
        )

        assert state is not None
        assert state.policy_revision == 2
        assert state.reconciled_revision == 0
        assert state.reconciliation_required is True

        db.rollback()

    finally:
        db.close()
        _cleanup(
            organization_ids
        )


def test_alert_policy_revision_bump_rolls_back_with_transaction():
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            setup_db
        )
        organization_ids.append(
            organization.id
        )

        setup_db.commit()

        bumped = bump_alert_policy_revision(
            execution_db,
            organization_id=organization.id,
        )

        assert bumped.policy_revision == 1

        execution_db.rollback()

        persisted = load_alert_policy_revision(
            verify_db,
            organization_id=organization.id,
        )

        assert persisted is None

    finally:
        setup_db.close()
        execution_db.close()
        verify_db.close()

        _cleanup(
            organization_ids
        )


def test_alert_policy_reconciliation_rolls_back_with_transaction():
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            setup_db
        )
        organization_ids.append(
            organization.id
        )

        setup_db.commit()

        initial = bump_alert_policy_revision(
            setup_db,
            organization_id=organization.id,
        )

        assert initial.policy_revision == 1

        setup_db.commit()

        reconciled = mark_alert_policy_reconciled(
            execution_db,
            organization_id=organization.id,
            policy_revision=initial.policy_revision,
        )

        assert reconciled.reconciled_revision == 1

        execution_db.rollback()

        persisted = load_alert_policy_revision(
            verify_db,
            organization_id=organization.id,
        )

        assert persisted is not None
        assert persisted.policy_revision == 1
        assert persisted.reconciled_revision == 0
        assert persisted.reconciliation_required is True

    finally:
        setup_db.close()
        execution_db.close()
        verify_db.close()

        _cleanup(
            organization_ids
        )


def test_alert_policy_revision_is_tenant_isolated():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        first_organization = _make_organization(
            db
        )
        second_organization = _make_organization(
            db
        )

        organization_ids.extend(
            [
                first_organization.id,
                second_organization.id,
            ]
        )

        db.commit()

        first_state = bump_alert_policy_revision(
            db,
            organization_id=first_organization.id,
        )

        assert first_state.policy_revision == 1

        second_state = load_alert_policy_revision(
            db,
            organization_id=second_organization.id,
        )

        assert second_state is None

        db.commit()

        first_persisted = load_alert_policy_revision(
            db,
            organization_id=first_organization.id,
        )
        second_persisted = load_alert_policy_revision(
            db,
            organization_id=second_organization.id,
        )

        assert first_persisted is not None
        assert first_persisted.policy_revision == 1
        assert second_persisted is None

    finally:
        db.close()
        _cleanup(
            organization_ids
        )
