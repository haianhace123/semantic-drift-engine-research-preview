from __future__ import annotations

import os
import uuid

import pytest

from src.core.database import SessionLocal
from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.models.organization import Organization
from src.services.lineage_impact_service import (
    analyze_lineage_impact,
)
from src.services.lineage_invalidation_service import (
    analyze_and_persist_lineage_invalidation_decisions,
)
from src.services.lineage_invalidation_policy_service import (
    DEFAULT_LINEAGE_INVALIDATION_POLICY,
)
from src.services.lineage_invalidation_policy_activation_service import (
    activate_lineage_invalidation_policy,
)
from src.services.lineage_invalidation_eligibility_service import (
    evaluate_lineage_invalidation_eligibility,
)
from src.services.lineage_invalidation_projection_service import (
    load_lineage_invalidation_decision_by_record_id,
)
from src.services.lineage_observation_persistence_service import (
    persist_lineage_observation,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="DB integration tests are disabled.",
)


def _create_organization(
    db,
    *,
    prefix: str,
) -> Organization:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        name=f"{prefix}-{suffix}",
        slug=f"{prefix}-{suffix}",
        billing_email=(
            f"{prefix}-{suffix}@example.test"
        ),
        subscription_tier="free",
        quota_urls=10,
        quota_requests=1000,
        is_active=True,
    )
    db.add(organization)
    db.flush()
    return organization


def _observation(
    *,
    source: str,
    snapshot: str,
    asset: str,
    asset_kind: str,
    revision: str,
    sequence: int,
    observation_kind: str = "materialization",
    dependencies: tuple[
        LineageRevisionReference,
        ...,
    ] = (),
) -> LineageObservation:
    return LineageObservation(
        observation_kind=observation_kind,
        source_external_id=source,
        snapshot_external_id=snapshot,
        asset_external_id=asset,
        asset_kind=asset_kind,
        revision_external_id=revision,
        sequence=sequence,
        dependencies=dependencies,
    )


def _dependency(
    asset: str,
    revision: str,
    dependency_type: str = "derived_from",
) -> LineageRevisionReference:
    return LineageRevisionReference(
        asset_external_id=asset,
        revision_external_id=revision,
        dependency_type=dependency_type,
    )


def _persist(
    db,
    *,
    organization_id: uuid.UUID,
    observation: LineageObservation,
) -> None:
    persist_lineage_observation(
        db,
        organization_id=organization_id,
        observation=observation,
    )


def _delete_organization(
    organization_id: uuid.UUID,
) -> None:
    with SessionLocal() as db:
        organization = (
            db.query(Organization)
            .filter(
                Organization.id
                == organization_id
            )
            .one_or_none()
        )

        if organization is not None:
            db.delete(organization)
            db.commit()


def _identity(
    source: str,
    asset: str,
    revision: str,
) -> LineageRevisionIdentity:
    return LineageRevisionIdentity(
        source_external_id=source,
        asset_external_id=asset,
        revision_external_id=revision,
    )


def test_lineage_impact_finds_direct_dependent() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="impact-direct",
            )
            organization_id = organization.id

            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="index-a",
                    asset_kind="index",
                    revision="index-a@v1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "chunk-a",
                            "chunk-a@v1",
                        ),
                    ),
                ),
            )
            db.commit()

            root = _identity(
                "source-a",
                "chunk-a",
                "chunk-a@v1",
            )
            target = _identity(
                "source-a",
                "index-a",
                "index-a@v1",
            )

            result = analyze_lineage_impact(
                db,
                organization_id=organization.id,
                root=root,
            )

            assert result.root == root
            assert result.impacted_revisions == (
                target,
            )
            assert len(result.paths) == 1
            assert result.paths[0].root == root
            assert result.paths[0].target == target
            assert result.paths[0].revisions == (
                root,
                target,
            )
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_lineage_impact_propagates_multiple_hops() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="impact-multihop",
            )
            organization_id = organization.id

            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="index-a",
                    asset_kind="index",
                    revision="index-a@v1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "chunk-a",
                            "chunk-a@v1",
                        ),
                    ),
                ),
            )
            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="agent-a",
                    asset_kind="agent",
                    revision="agent-a@v1",
                    sequence=1,
                    observation_kind="consumption",
                    dependencies=(
                        _dependency(
                            "index-a",
                            "index-a@v1",
                            "retrieves_from",
                        ),
                    ),
                ),
            )
            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="workflow-a",
                    asset_kind="workflow",
                    revision="workflow-a@v1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "agent-a",
                            "agent-a@v1",
                        ),
                    ),
                ),
            )
            db.commit()

            root = _identity(
                "source-a",
                "chunk-a",
                "chunk-a@v1",
            )
            index = _identity(
                "source-a",
                "index-a",
                "index-a@v1",
            )
            agent = _identity(
                "source-a",
                "agent-a",
                "agent-a@v1",
            )
            workflow = _identity(
                "source-a",
                "workflow-a",
                "workflow-a@v1",
            )

            result = analyze_lineage_impact(
                db,
                organization_id=organization.id,
                root=root,
            )

            assert result.impacted_revisions == (
                agent,
                index,
                workflow,
            )

            paths = {
                path.target: path.revisions
                for path in result.paths
            }

            assert paths[index] == (
                root,
                index,
            )
            assert paths[agent] == (
                root,
                index,
                agent,
            )
            assert paths[workflow] == (
                root,
                index,
                agent,
                workflow,
            )
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_lineage_impact_excludes_historical_downstream_revision() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="impact-historical",
            )
            organization_id = organization.id

            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="index-a",
                    asset_kind="index",
                    revision="index-a@v1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "chunk-old",
                            "chunk-old@v1",
                        ),
                    ),
                ),
            )
            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-2",
                    asset="index-a",
                    asset_kind="index",
                    revision="index-a@v2",
                    sequence=2,
                    dependencies=(
                        _dependency(
                            "chunk-new",
                            "chunk-new@v2",
                        ),
                    ),
                ),
            )
            db.commit()

            old_result = analyze_lineage_impact(
                db,
                organization_id=organization.id,
                root=_identity(
                    "source-a",
                    "chunk-old",
                    "chunk-old@v1",
                ),
            )
            new_result = analyze_lineage_impact(
                db,
                organization_id=organization.id,
                root=_identity(
                    "source-a",
                    "chunk-new",
                    "chunk-new@v2",
                ),
            )

            assert old_result.impacted_revisions == ()
            assert old_result.paths == ()
            assert new_result.impacted_revisions == (
                _identity(
                    "source-a",
                    "index-a",
                    "index-a@v2",
                ),
            )
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_lineage_impact_uses_deterministic_shortest_path() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="impact-diamond",
            )
            organization_id = organization.id

            for asset in (
                "index-b",
                "index-a",
            ):
                _persist(
                    db,
                    organization_id=organization.id,
                    observation=_observation(
                        source="source-a",
                        snapshot="snapshot-1",
                        asset=asset,
                        asset_kind="index",
                        revision=f"{asset}@v1",
                        sequence=1,
                        dependencies=(
                            _dependency(
                                "chunk-root",
                                "chunk-root@v1",
                            ),
                        ),
                    ),
                )

            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="agent-target",
                    asset_kind="agent",
                    revision="agent-target@v1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "index-b",
                            "index-b@v1",
                        ),
                        _dependency(
                            "index-a",
                            "index-a@v1",
                        ),
                    ),
                ),
            )
            db.commit()

            root = _identity(
                "source-a",
                "chunk-root",
                "chunk-root@v1",
            )
            index_a = _identity(
                "source-a",
                "index-a",
                "index-a@v1",
            )
            target = _identity(
                "source-a",
                "agent-target",
                "agent-target@v1",
            )

            first = analyze_lineage_impact(
                db,
                organization_id=organization.id,
                root=root,
            )
            second = analyze_lineage_impact(
                db,
                organization_id=organization.id,
                root=root,
            )

            first_paths = {
                path.target: path.revisions
                for path in first.paths
            }
            second_paths = {
                path.target: path.revisions
                for path in second.paths
            }

            assert first == second
            assert first_paths[target] == (
                root,
                index_a,
                target,
            )
            assert second_paths[target] == (
                root,
                index_a,
                target,
            )
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_lineage_impact_is_cycle_safe() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="impact-cycle",
            )
            organization_id = organization.id

            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="index-a",
                    asset_kind="index",
                    revision="index-a@v1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "agent-a",
                            "agent-a@v1",
                        ),
                        _dependency(
                            "chunk-root",
                            "chunk-root@v1",
                        ),
                    ),
                ),
            )
            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="agent-a",
                    asset_kind="agent",
                    revision="agent-a@v1",
                    sequence=1,
                    observation_kind="consumption",
                    dependencies=(
                        _dependency(
                            "index-a",
                            "index-a@v1",
                            "retrieves_from",
                        ),
                    ),
                ),
            )
            db.commit()

            root = _identity(
                "source-a",
                "chunk-root",
                "chunk-root@v1",
            )
            index = _identity(
                "source-a",
                "index-a",
                "index-a@v1",
            )
            agent = _identity(
                "source-a",
                "agent-a",
                "agent-a@v1",
            )

            result = analyze_lineage_impact(
                db,
                organization_id=organization.id,
                root=root,
            )

            assert result.impacted_revisions == (
                agent,
                index,
            )
            assert {
                path.target: path.revisions
                for path in result.paths
            } == {
                index: (
                    root,
                    index,
                ),
                agent: (
                    root,
                    index,
                    agent,
                ),
            }
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_lineage_impact_is_tenant_scoped() -> None:
    first_organization_id = None
    second_organization_id = None

    try:
        with SessionLocal() as db:
            first = _create_organization(
                db,
                prefix="impact-tenant-a",
            )
            second = _create_organization(
                db,
                prefix="impact-tenant-b",
            )
            first_organization_id = first.id
            second_organization_id = second.id

            _persist(
                db,
                organization_id=first.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="index-first",
                    asset_kind="index",
                    revision="index-first@v1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "chunk-shared",
                            "chunk-shared@v1",
                        ),
                    ),
                ),
            )
            _persist(
                db,
                organization_id=second.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="index-second",
                    asset_kind="index",
                    revision="index-second@v1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "chunk-shared",
                            "chunk-shared@v1",
                        ),
                    ),
                ),
            )
            db.commit()

            root = _identity(
                "source-a",
                "chunk-shared",
                "chunk-shared@v1",
            )

            first_result = analyze_lineage_impact(
                db,
                organization_id=first.id,
                root=root,
            )
            second_result = analyze_lineage_impact(
                db,
                organization_id=second.id,
                root=root,
            )

            assert first_result.impacted_revisions == (
                _identity(
                    "source-a",
                    "index-first",
                    "index-first@v1",
                ),
            )
            assert second_result.impacted_revisions == (
                _identity(
                    "source-a",
                    "index-second",
                    "index-second@v1",
                ),
            )
    finally:
        if first_organization_id is not None:
            _delete_organization(first_organization_id)
        if second_organization_id is not None:
            _delete_organization(second_organization_id)


def test_lineage_impact_does_not_cross_sources() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="impact-source",
            )
            organization_id = organization.id

            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-a",
                    asset="index-a",
                    asset_kind="index",
                    revision="index-a@v1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "chunk-shared",
                            "chunk-shared@v1",
                        ),
                    ),
                ),
            )
            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-b",
                    snapshot="snapshot-b",
                    asset="index-b",
                    asset_kind="index",
                    revision="index-b@v1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "chunk-shared",
                            "chunk-shared@v1",
                        ),
                    ),
                ),
            )
            db.commit()

            result = analyze_lineage_impact(
                db,
                organization_id=organization.id,
                root=_identity(
                    "source-a",
                    "chunk-shared",
                    "chunk-shared@v1",
                ),
            )

            assert result.impacted_revisions == (
                _identity(
                    "source-a",
                    "index-a",
                    "index-a@v1",
                ),
            )
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_lineage_impact_is_exact_revision_scoped() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="impact-revision",
            )
            organization_id = organization.id

            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-2",
                    asset="index-a",
                    asset_kind="index",
                    revision="index-a@v2",
                    sequence=2,
                    dependencies=(
                        _dependency(
                            "chunk-a",
                            "chunk-a@v2",
                        ),
                    ),
                ),
            )
            db.commit()

            old_result = analyze_lineage_impact(
                db,
                organization_id=organization.id,
                root=_identity(
                    "source-a",
                    "chunk-a",
                    "chunk-a@v1",
                ),
            )
            current_result = analyze_lineage_impact(
                db,
                organization_id=organization.id,
                root=_identity(
                    "source-a",
                    "chunk-a",
                    "chunk-a@v2",
                ),
            )

            assert old_result.impacted_revisions == ()
            assert current_result.impacted_revisions == (
                _identity(
                    "source-a",
                    "index-a",
                    "index-a@v2",
                ),
            )
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_lineage_impact_deduplicates_revision_identity_across_observation_kinds():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="impact-observation-kinds",
            )
            organization_id = organization.id

            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="agent-a",
                    asset_kind="agent",
                    revision="agent-a@v1",
                    sequence=1,
                    observation_kind="materialization",
                    dependencies=(
                        _dependency(
                            "chunk-root",
                            "chunk-root@v1",
                        ),
                    ),
                ),
            )
            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="agent-a",
                    asset_kind="agent",
                    revision="agent-a@v1",
                    sequence=1,
                    observation_kind="consumption",
                    dependencies=(
                        _dependency(
                            "chunk-root",
                            "chunk-root@v1",
                            "consumes",
                        ),
                    ),
                ),
            )
            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="workflow-a",
                    asset_kind="workflow",
                    revision="workflow-a@v1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "agent-a",
                            "agent-a@v1",
                        ),
                    ),
                ),
            )
            db.commit()

            root = _identity(
                "source-a",
                "chunk-root",
                "chunk-root@v1",
            )
            agent = _identity(
                "source-a",
                "agent-a",
                "agent-a@v1",
            )
            workflow = _identity(
                "source-a",
                "workflow-a",
                "workflow-a@v1",
            )

            result = analyze_lineage_impact(
                db,
                organization_id=organization.id,
                root=root,
            )

            assert result.impacted_revisions == (
                agent,
                workflow,
            )
            assert {
                path.target: path.revisions
                for path in result.paths
            } == {
                agent: (
                    root,
                    agent,
                ),
                workflow: (
                    root,
                    agent,
                    workflow,
                ),
            }
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_lineage_revision_identity_normalizes_and_rejects_empty_values():
    assert _identity(
        " source-a ",
        " chunk-a ",
        " chunk-a@v1 ",
    ) == _identity(
        "source-a",
        "chunk-a",
        "chunk-a@v1",
    )

    with pytest.raises(
        ValueError,
        match="source_external_id must not be empty",
    ):
        _identity(
            " ",
            "chunk-a",
            "chunk-a@v1",
        )

def test_retired_intermediate_still_exposes_current_downstream_consumer() -> None:
    """Updating one index does not repair another index pinned to its old revision."""
    organization_id = None
    try:
        with SessionLocal() as db:
            organization = _create_organization(db, prefix="impact-retired-hop")
            organization_id = organization.id
            for observation in (
                _observation(
                    source="source-a", snapshot="snapshot-1", asset="index-a",
                    asset_kind="index", revision="index-a@v1", sequence=1,
                    dependencies=(_dependency("policy-a", "policy-a@v1"),),
                ),
                _observation(
                    source="source-a", snapshot="snapshot-2", asset="index-a",
                    asset_kind="index", revision="index-a@v2", sequence=2,
                    dependencies=(_dependency("policy-a", "policy-a@v2"),),
                ),
                _observation(
                    source="source-a", snapshot="snapshot-1", asset="index-b",
                    asset_kind="index", revision="index-b@v1", sequence=1,
                    observation_kind="materialization",
                    dependencies=(_dependency("index-a", "index-a@v1"),),
                ),
            ):
                _persist(db, organization_id=organization.id, observation=observation)
            db.commit()

            root = _identity("source-a", "policy-a", "policy-a@v1")
            old_index = _identity("source-a", "index-a", "index-a@v1")
            downstream_index = _identity("source-a", "index-b", "index-b@v1")
            initial = analyze_lineage_impact(
                db, organization_id=organization.id, root=root,
            )
            assert initial.impacted_revisions == (downstream_index,)
            assert initial.paths[0].revisions == (root, old_index, downstream_index)

            first = analyze_and_persist_lineage_invalidation_decisions(
                db,
                organization_id=organization.id,
                root=root,
                policy_identity=DEFAULT_LINEAGE_INVALIDATION_POLICY,
            )
            assert len(first.decisions) == 1
            assert first.decisions[0].created is True
            assert first.decisions[0].record.disposition == "invalidate"
            assert [
                row.revision_external_id
                for row in sorted(
                    first.decisions[0].record.causal_path,
                    key=lambda row: row.ordinal,
                )
            ] == ["policy-a@v1", "index-a@v1", "index-b@v1"]

            replay = analyze_and_persist_lineage_invalidation_decisions(
                db,
                organization_id=organization.id,
                root=root,
                policy_identity=DEFAULT_LINEAGE_INVALIDATION_POLICY,
            )
            assert len(replay.decisions) == 1
            assert replay.decisions[0].created is False
            assert replay.decisions[0].record.id == first.decisions[0].record.id

            activate_lineage_invalidation_policy(
                db,
                organization_id=organization.id,
                policy=DEFAULT_LINEAGE_INVALIDATION_POLICY,
            )
            persisted_view = load_lineage_invalidation_decision_by_record_id(
                db,
                organization_id=organization.id,
                record_id=first.decisions[0].record.id,
            )
            assert persisted_view is not None
            eligible = evaluate_lineage_invalidation_eligibility(
                db,
                organization_id=organization.id,
                decision=persisted_view.decision,
            )
            assert eligible.eligible is True

            # The downstream index is rebuilt against index-a v2.
            _persist(
                db, organization_id=organization.id,
                observation=_observation(
                    source="source-a", snapshot="snapshot-3", asset="index-b",
                    asset_kind="index", revision="index-b@v2", sequence=2,
                    observation_kind="materialization",
                    dependencies=(_dependency("index-a", "index-a@v2"),),
                ),
            )
            db.commit()
            final = analyze_lineage_impact(
                db, organization_id=organization.id, root=root,
            )
            assert final.impacted_revisions == ()
            eligibility_after_repair = evaluate_lineage_invalidation_eligibility(
                db,
                organization_id=organization.id,
                decision=persisted_view.decision,
            )
            assert eligibility_after_repair.eligible is False
            assert eligibility_after_repair.reason_code == "target_not_current"
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)
