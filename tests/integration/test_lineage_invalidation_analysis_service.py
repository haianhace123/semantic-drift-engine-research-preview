from __future__ import annotations

import os
import uuid

import pytest

from src.core.database import SessionLocal
from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.models.lineage_invalidation_decision import (
    LineageInvalidationDecisionRecord,
)
from src.models.organization import Organization
from src.services.lineage_invalidation_policy_service import (
    DEFAULT_LINEAGE_INVALIDATION_POLICY,
)
from src.services.lineage_invalidation_service import (
    LineageInvalidationTargetFactsConflictError,
    analyze_and_persist_lineage_invalidation_decisions,
)
from src.services.lineage_observation_persistence_service import (
    persist_lineage_observation,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _create_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        name=f"lineage-invalidation-analysis-{suffix}",
        slug=f"lineage-invalidation-analysis-{suffix}",
        billing_email=f"{suffix}@example.test",
        subscription_tier="free",
        quota_urls=10,
        quota_requests=1000,
        is_active=True,
    )
    db.add(organization)
    db.flush()
    return organization


def _root() -> LineageRevisionIdentity:
    return LineageRevisionIdentity(
        source_external_id="source-a",
        asset_external_id="chunk-a",
        revision_external_id="chunk-a@v1",
    )


def _dependency() -> LineageRevisionReference:
    return LineageRevisionReference(
        asset_external_id="chunk-a",
        revision_external_id="chunk-a@v1",
        dependency_type="indexed_from",
    )


def _index_materialization() -> LineageObservation:
    return LineageObservation(
        observation_kind="materialization",
        source_external_id="source-a",
        snapshot_external_id="snapshot-1",
        asset_external_id="index-a",
        asset_kind="index",
        revision_external_id="index-a@v1",
        sequence=1,
        dependencies=(_dependency(),),
    )


def _delete_organization(
    organization_id: uuid.UUID,
) -> None:
    with SessionLocal() as db:
        organization = (
            db.query(Organization)
            .filter(
                Organization.id == organization_id,
            )
            .one_or_none()
        )

        if organization is not None:
            db.delete(organization)
            db.commit()


def test_analysis_persists_exact_index_invalidation_and_replays() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(db)
            organization_id = organization.id

            persist_lineage_observation(
                db,
                organization_id=organization.id,
                observation=_index_materialization(),
            )
            db.commit()

        with SessionLocal() as db:
            first = (
                analyze_and_persist_lineage_invalidation_decisions(
                    db,
                    organization_id=organization_id,
                    root=_root(),
                    policy_identity=(
                        DEFAULT_LINEAGE_INVALIDATION_POLICY
                    ),
                )
            )

            assert first.impact.impacted_revisions == (
                LineageRevisionIdentity(
                    source_external_id="source-a",
                    asset_external_id="index-a",
                    revision_external_id="index-a@v1",
                ),
            )
            assert len(first.decisions) == 1
            assert first.decisions[0].created is True

            decision = first.decisions[0].record
            assert decision.disposition == "invalidate"
            assert (
                decision.reason_code
                == "causal_dependency_changed"
            )

            db.commit()

        with SessionLocal() as db:
            replay = (
                analyze_and_persist_lineage_invalidation_decisions(
                    db,
                    organization_id=organization_id,
                    root=_root(),
                    policy_identity=(
                        DEFAULT_LINEAGE_INVALIDATION_POLICY
                    ),
                )
            )

            assert len(replay.decisions) == 1
            assert replay.decisions[0].created is False

            count = (
                db.query(LineageInvalidationDecisionRecord)
                .filter(
                    LineageInvalidationDecisionRecord.organization_id
                    == organization_id,
                )
                .count()
            )
            assert count == 1
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_analysis_fails_closed_on_conflicting_exact_asset_kinds() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(db)
            organization_id = organization.id

            persist_lineage_observation(
                db,
                organization_id=organization.id,
                observation=_index_materialization(),
            )
            persist_lineage_observation(
                db,
                organization_id=organization.id,
                observation=LineageObservation(
                    observation_kind="consumption",
                    source_external_id="source-a",
                    snapshot_external_id="snapshot-1",
                    asset_external_id="index-a",
                    asset_kind="agent",
                    revision_external_id="index-a@v1",
                    sequence=1,
                    dependencies=(_dependency(),),
                ),
            )
            db.commit()

        with SessionLocal() as db:
            with pytest.raises(
                LineageInvalidationTargetFactsConflictError,
                match="conflicting asset kinds",
            ):
                analyze_and_persist_lineage_invalidation_decisions(
                    db,
                    organization_id=organization_id,
                    root=_root(),
                    policy_identity=(
                        DEFAULT_LINEAGE_INVALIDATION_POLICY
                    ),
                )

            count = (
                db.query(LineageInvalidationDecisionRecord)
                .filter(
                    LineageInvalidationDecisionRecord.organization_id
                    == organization_id,
                )
                .count()
            )
            assert count == 0
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)