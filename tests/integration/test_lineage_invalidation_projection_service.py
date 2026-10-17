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
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.models.lineage_invalidation_decision import (
    LineageInvalidationDecisionRecord,
)
from src.models.organization import Organization
from src.services.lineage_invalidation_persistence_service import (
    persist_lineage_invalidation_decision,
)
from src.services.lineage_invalidation_projection_service import (
    load_lineage_invalidation_decision,
    load_lineage_invalidation_decision_by_record_id,
    load_lineage_invalidation_decisions_for_root,
    load_lineage_invalidation_decisions_for_target,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        id=uuid.uuid4(),
        name=f"lineage-invalidation-projection-{suffix}",
        slug=f"lineage-invalidation-projection-{suffix}",
        billing_email=f"{suffix}@example.com",
    )
    db.add(organization)
    db.flush()
    return organization


def _revision(
    asset_external_id: str,
    *,
    source_external_id: str = "policy-source",
    revision_external_id: str | None = None,
) -> LineageRevisionIdentity:
    return LineageRevisionIdentity(
        source_external_id=source_external_id,
        asset_external_id=asset_external_id,
        revision_external_id=(
            revision_external_id
            or f"{asset_external_id}@v1"
        ),
    )


def _decision(
    *,
    root_asset: str = "chunk-a",
    target_asset: str = "agent-a",
    policy_revision: int = 7,
    disposition: str = "invalidate",
    reason_code: str = "causal_dependency_changed",
    intermediate_asset: str = "index-a",
) -> LineageInvalidationDecision:
    root = _revision(root_asset)
    intermediate = _revision(intermediate_asset)
    target = _revision(target_asset)

    return LineageInvalidationDecision(
        root=root,
        target=target,
        policy=LineageInvalidationPolicyIdentity(
            policy_external_id="default-lineage-policy",
            revision=policy_revision,
        ),
        disposition=disposition,
        reason_code=reason_code,
        causal_path=(
            root,
            intermediate,
            target,
        ),
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
            delete(LineageInvalidationDecisionRecord).where(
                LineageInvalidationDecisionRecord.organization_id.in_(
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


def test_record_id_lookup_reconstructs_exact_persisted_decision() -> None:
    organization_ids: list[uuid.UUID] = []
    db = SessionLocal()

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        decision = _decision()

        persisted = persist_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            decision=decision,
        )
        db.commit()

        loaded = load_lineage_invalidation_decision_by_record_id(
            db,
            organization_id=organization.id,
            record_id=persisted.record.id,
        )

        assert loaded is not None
        assert loaded.record_id == persisted.record.id
        assert loaded.organization_id == organization.id
        assert loaded.decision == decision
        assert loaded.created_at == persisted.record.created_at
    finally:
        db.close()
        _cleanup(organization_ids)


def test_record_id_lookup_is_tenant_scoped() -> None:
    organization_ids: list[uuid.UUID] = []
    db = SessionLocal()

    try:
        owner = _make_organization(db)
        other = _make_organization(db)
        organization_ids.extend(
            [
                owner.id,
                other.id,
            ]
        )

        persisted = persist_lineage_invalidation_decision(
            db,
            organization_id=owner.id,
            decision=_decision(),
        )
        db.commit()

        loaded = load_lineage_invalidation_decision_by_record_id(
            db,
            organization_id=other.id,
            record_id=persisted.record.id,
        )

        assert loaded is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_record_id_lookup_returns_none_for_unknown_record() -> None:
    organization_ids: list[uuid.UUID] = []
    db = SessionLocal()

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        loaded = load_lineage_invalidation_decision_by_record_id(
            db,
            organization_id=organization.id,
            record_id=uuid.uuid4(),
        )

        assert loaded is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_exact_lookup_reconstructs_persisted_decision() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        decision = _decision()

        persisted = persist_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            decision=decision,
        )
        record_id = persisted.record.id
        db.commit()

        db.expire_all()

        result = load_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            root=decision.root,
            target=decision.target,
            policy=decision.policy,
        )

        assert result is not None
        assert result.record_id == record_id
        assert result.organization_id == organization.id
        assert result.decision == decision
        assert result.created_at is not None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_exact_lookup_does_not_infer_other_policy_revision() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        decision = _decision(
            policy_revision=7,
        )

        persist_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            decision=decision,
        )
        db.commit()

        missing = load_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            root=decision.root,
            target=decision.target,
            policy=LineageInvalidationPolicyIdentity(
                policy_external_id=decision.policy.policy_external_id,
                revision=8,
            ),
        )

        assert missing is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_exact_lookup_is_tenant_scoped() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization_a = _make_organization(db)
        organization_b = _make_organization(db)
        organization_ids.extend(
            [
                organization_a.id,
                organization_b.id,
            ]
        )
        db.commit()

        decision = _decision()

        persist_lineage_invalidation_decision(
            db,
            organization_id=organization_a.id,
            decision=decision,
        )
        db.commit()

        result = load_lineage_invalidation_decision(
            db,
            organization_id=organization_b.id,
            root=decision.root,
            target=decision.target,
            policy=decision.policy,
        )

        assert result is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_target_history_returns_exact_target_only() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        first = _decision(
            root_asset="chunk-a",
            target_asset="agent-a",
            policy_revision=7,
        )
        second = _decision(
            root_asset="chunk-b",
            target_asset="agent-a",
            policy_revision=8,
            intermediate_asset="index-b",
        )
        other_target = _decision(
            root_asset="chunk-c",
            target_asset="agent-b",
            policy_revision=9,
            intermediate_asset="index-c",
        )

        for decision in (
            first,
            second,
            other_target,
        ):
            persist_lineage_invalidation_decision(
                db,
                organization_id=organization.id,
                decision=decision,
            )
            db.flush()

        db.commit()

        results = load_lineage_invalidation_decisions_for_target(
            db,
            organization_id=organization.id,
            target=first.target,
        )

        assert {
            result.decision
            for result in results
        } == {
            first,
            second,
        }

        assert tuple(
            (
                result.created_at,
                result.record_id,
            )
            for result in results
        ) == tuple(
            sorted(
                (
                    result.created_at,
                    result.record_id,
                )
                for result in results
            )
        )
    finally:
        db.close()
        _cleanup(organization_ids)


def test_root_history_returns_exact_root_only() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        first = _decision(
            root_asset="chunk-a",
            target_asset="agent-a",
            policy_revision=7,
        )
        second = _decision(
            root_asset="chunk-a",
            target_asset="agent-b",
            policy_revision=8,
            intermediate_asset="index-b",
        )
        other_root = _decision(
            root_asset="chunk-b",
            target_asset="agent-c",
            policy_revision=9,
            intermediate_asset="index-c",
        )

        for decision in (
            first,
            second,
            other_root,
        ):
            persist_lineage_invalidation_decision(
                db,
                organization_id=organization.id,
                decision=decision,
            )
            db.flush()

        db.commit()

        results = load_lineage_invalidation_decisions_for_root(
            db,
            organization_id=organization.id,
            root=first.root,
        )

        assert {
            result.decision
            for result in results
        } == {
            first,
            second,
        }

        assert tuple(
            (
                result.created_at,
                result.record_id,
            )
            for result in results
        ) == tuple(
            sorted(
                (
                    result.created_at,
                    result.record_id,
                )
                for result in results
            )
        )
    finally:
        db.close()
        _cleanup(organization_ids)


def test_history_preserves_exact_ordered_causal_paths() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        decision = _decision(
            intermediate_asset="index-z",
        )

        persist_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            decision=decision,
        )
        db.commit()

        results = load_lineage_invalidation_decisions_for_target(
            db,
            organization_id=organization.id,
            target=decision.target,
        )

        assert len(results) == 1
        assert results[0].decision.causal_path == (
            _revision("chunk-a"),
            _revision("index-z"),
            _revision("agent-a"),
        )
    finally:
        db.close()
        _cleanup(organization_ids)


def test_projection_reads_do_not_mutate_persisted_history() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        decision = _decision()

        persist_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            decision=decision,
        )
        db.commit()

        before = (
            db.query(LineageInvalidationDecisionRecord)
            .filter(
                LineageInvalidationDecisionRecord.organization_id
                == organization.id
            )
            .count()
        )

        exact = load_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            root=decision.root,
            target=decision.target,
            policy=decision.policy,
        )
        target_history = (
            load_lineage_invalidation_decisions_for_target(
                db,
                organization_id=organization.id,
                target=decision.target,
            )
        )
        root_history = (
            load_lineage_invalidation_decisions_for_root(
                db,
                organization_id=organization.id,
                root=decision.root,
            )
        )

        after = (
            db.query(LineageInvalidationDecisionRecord)
            .filter(
                LineageInvalidationDecisionRecord.organization_id
                == organization.id
            )
            .count()
        )

        assert exact is not None
        assert len(target_history) == 1
        assert len(root_history) == 1
        assert after == before
    finally:
        db.close()
        _cleanup(organization_ids)