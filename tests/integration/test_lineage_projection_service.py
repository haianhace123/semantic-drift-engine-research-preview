from __future__ import annotations

import os
import uuid

import pytest

from src.core.database import SessionLocal
from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.models.lineage_observation import LineageObservationRecord
from src.models.organization import Organization
from src.services.lineage_observation_persistence_service import (
    persist_lineage_observation,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.services.lineage_projection_service import (
    load_current_lineage_observation,
    load_current_lineage_observations,
    load_exact_revision_dependents,
    load_exact_revision_dependents_batch,
    load_lineage_revision,
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


def test_current_projection_uses_highest_sequence_out_of_order():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="lineage-current",
            )
            organization_id = organization.id

            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-3",
                    asset="chunk-a",
                    asset_kind="chunk",
                    revision="chunk-a-r3",
                    sequence=3,
                ),
            )
            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="chunk-a",
                    asset_kind="chunk",
                    revision="chunk-a-r1",
                    sequence=1,
                ),
            )
            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-2",
                    asset="chunk-a",
                    asset_kind="chunk",
                    revision="chunk-a-r2",
                    sequence=2,
                ),
            )
            db.commit()

        with SessionLocal() as db:
            current = load_current_lineage_observations(
                db,
                organization_id=organization_id,
            )

            assert len(current) == 1
            assert current[0].revision_external_id == "chunk-a-r3"
            assert current[0].sequence == 3
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_current_projection_keeps_observation_kinds_independent():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="lineage-kinds",
            )
            organization_id = organization.id

            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-4",
                    asset="agent-a",
                    asset_kind="agent",
                    revision="agent-materialized-r4",
                    sequence=4,
                ),
            )
            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-9",
                    asset="agent-a",
                    asset_kind="agent",
                    revision="agent-consumed-r9",
                    sequence=9,
                    observation_kind="consumption",
                    dependencies=(
                        _dependency(
                            "index-a",
                            "index-a-r7",
                            "retrieves_from",
                        ),
                    ),
                ),
            )
            db.commit()

        with SessionLocal() as db:
            current = load_current_lineage_observations(
                db,
                organization_id=organization_id,
            )

            assert len(current) == 2

            by_kind = {
                observation.observation_kind: observation
                for observation in current
            }

            assert (
                by_kind["materialization"].revision_external_id
                == "agent-materialized-r4"
            )
            assert (
                by_kind["consumption"].revision_external_id
                == "agent-consumed-r9"
            )
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_exact_revision_lookup_returns_historical_revision():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="lineage-revision",
            )
            organization_id = organization.id

            for sequence in (1, 2):
                _persist(
                    db,
                    organization_id=organization.id,
                    observation=_observation(
                        source="source-a",
                        snapshot=f"snapshot-{sequence}",
                        asset="chunk-a",
                        asset_kind="chunk",
                        revision=f"chunk-a-r{sequence}",
                        sequence=sequence,
                    ),
                )

            db.commit()

        with SessionLocal() as db:
            revisions = load_lineage_revision(
                db,
                organization_id=organization_id,
                identity=LineageRevisionIdentity(
                    source_external_id="source-a",
                    asset_external_id="chunk-a",
                    revision_external_id="chunk-a-r1",
                ),
            )

            assert len(revisions) == 1
            assert revisions[0].sequence == 1
            assert revisions[0].revision_external_id == "chunk-a-r1"
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_current_dependents_preserve_exact_upstream_revision():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="lineage-exact",
            )
            organization_id = organization.id

            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="chunk-a",
                    asset_kind="chunk",
                    revision="chunk-a-r1",
                    sequence=1,
                ),
            )
            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="index-a",
                    asset_kind="index",
                    revision="index-a-r1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "chunk-a",
                            "chunk-a-r1",
                            "indexed_from",
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
                    asset="chunk-a",
                    asset_kind="chunk",
                    revision="chunk-a-r2",
                    sequence=2,
                ),
            )
            db.commit()

        with SessionLocal() as db:
            old_revision_dependents = (
                load_exact_revision_dependents(
                    db,
                    organization_id=organization_id,
                    source_external_id="source-a",
                    asset_external_id="chunk-a",
                    revision_external_id="chunk-a-r1",
                )
            )
            new_revision_dependents = (
                load_exact_revision_dependents(
                    db,
                    organization_id=organization_id,
                    source_external_id="source-a",
                    asset_external_id="chunk-a",
                    revision_external_id="chunk-a-r2",
                )
            )

            assert len(old_revision_dependents) == 1
            assert (
                old_revision_dependents[0].revision_external_id
                == "index-a-r1"
            )
            assert new_revision_dependents == ()
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_newer_downstream_revision_replaces_current_dependency():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="lineage-replace",
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
                    revision="index-a-r1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "chunk-a",
                            "chunk-a-r1",
                            "indexed_from",
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
                    revision="index-a-r2",
                    sequence=2,
                    dependencies=(
                        _dependency(
                            "chunk-a",
                            "chunk-a-r2",
                            "indexed_from",
                        ),
                    ),
                ),
            )
            db.commit()

        with SessionLocal() as db:
            current_old = load_exact_revision_dependents(
                db,
                organization_id=organization_id,
                source_external_id="source-a",
                asset_external_id="chunk-a",
                revision_external_id="chunk-a-r1",
            )
            historical_old = load_exact_revision_dependents(
                db,
                organization_id=organization_id,
                source_external_id="source-a",
                asset_external_id="chunk-a",
                revision_external_id="chunk-a-r1",
                current_only=False,
            )
            current_new = load_exact_revision_dependents(
                db,
                organization_id=organization_id,
                source_external_id="source-a",
                asset_external_id="chunk-a",
                revision_external_id="chunk-a-r2",
            )

            assert current_old == ()

            assert len(historical_old) == 1
            assert (
                historical_old[0].revision_external_id
                == "index-a-r1"
            )

            assert len(current_new) == 1
            assert (
                current_new[0].revision_external_id
                == "index-a-r2"
            )
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_projection_is_tenant_scoped_for_same_external_ids():
    organization_a_id = None
    organization_b_id = None

    try:
        with SessionLocal() as db:
            organization_a = _create_organization(
                db,
                prefix="lineage-tenant-a",
            )
            organization_b = _create_organization(
                db,
                prefix="lineage-tenant-b",
            )
            organization_a_id = organization_a.id
            organization_b_id = organization_b.id

            _persist(
                db,
                organization_id=organization_a.id,
                observation=_observation(
                    source="shared-source",
                    snapshot="snapshot-a",
                    asset="shared-index",
                    asset_kind="index",
                    revision="index-a",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "shared-chunk",
                            "chunk-r1",
                        ),
                    ),
                ),
            )
            _persist(
                db,
                organization_id=organization_b.id,
                observation=_observation(
                    source="shared-source",
                    snapshot="snapshot-b",
                    asset="shared-index",
                    asset_kind="index",
                    revision="index-b",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "shared-chunk",
                            "chunk-r1",
                        ),
                    ),
                ),
            )
            db.commit()

        with SessionLocal() as db:
            organization_a_dependents = (
                load_exact_revision_dependents(
                    db,
                    organization_id=organization_a_id,
                    source_external_id="shared-source",
                    asset_external_id="shared-chunk",
                    revision_external_id="chunk-r1",
                )
            )
            organization_b_dependents = (
                load_exact_revision_dependents(
                    db,
                    organization_id=organization_b_id,
                    source_external_id="shared-source",
                    asset_external_id="shared-chunk",
                    revision_external_id="chunk-r1",
                )
            )

            assert [
                item.revision_external_id
                for item in organization_a_dependents
            ] == ["index-a"]

            assert [
                item.revision_external_id
                for item in organization_b_dependents
            ] == ["index-b"]
    finally:
        if organization_a_id is not None:
            _delete_organization(organization_a_id)
        if organization_b_id is not None:
            _delete_organization(organization_b_id)


def test_source_filter_does_not_mix_same_asset_across_sources():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="lineage-source",
            )
            organization_id = organization.id

            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-a",
                    asset="shared-asset",
                    asset_kind="chunk",
                    revision="source-a-r1",
                    sequence=1,
                ),
            )
            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-b",
                    snapshot="snapshot-b",
                    asset="shared-asset",
                    asset_kind="chunk",
                    revision="source-b-r8",
                    sequence=8,
                ),
            )
            db.commit()

        with SessionLocal() as db:
            source_a = load_current_lineage_observations(
                db,
                organization_id=organization_id,
                source_external_id="source-a",
            )

            assert len(source_a) == 1
            assert source_a[0].source_external_id == "source-a"
            assert source_a[0].revision_external_id == "source-a-r1"
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_dependencies_are_returned_in_deterministic_order():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="lineage-order",
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
                    revision="agent-a-r1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "index-z",
                            "revision-2",
                            "retrieves_from",
                        ),
                        _dependency(
                            "index-a",
                            "revision-3",
                            "retrieves_from",
                        ),
                        _dependency(
                            "index-a",
                            "revision-1",
                            "consumes",
                        ),
                    ),
                ),
            )
            db.commit()

        with SessionLocal() as db:
            current = load_current_lineage_observations(
                db,
                organization_id=organization_id,
            )

            assert len(current) == 1
            assert [
                (
                    dependency.asset_external_id,
                    dependency.revision_external_id,
                    dependency.dependency_type,
                )
                for dependency in current[0].dependencies
            ] == [
                (
                    "index-a",
                    "revision-1",
                    "consumes",
                ),
                (
                    "index-a",
                    "revision-3",
                    "retrieves_from",
                ),
                (
                    "index-z",
                    "revision-2",
                    "retrieves_from",
                ),
            ]
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_exact_revision_lookup_does_not_infer_latest():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="lineage-no-latest",
            )
            organization_id = organization.id

            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-1",
                    asset="chunk-a",
                    asset_kind="chunk",
                    revision="chunk-a-r1",
                    sequence=1,
                ),
            )
            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-2",
                    asset="chunk-a",
                    asset_kind="chunk",
                    revision="chunk-a-r2",
                    sequence=2,
                ),
            )
            db.commit()

        with SessionLocal() as db:
            missing = load_lineage_revision(
                db,
                organization_id=organization_id,
                identity=LineageRevisionIdentity(
                    source_external_id="source-a",
                    asset_external_id="chunk-a",
                    revision_external_id="chunk-a-missing",
                ),
            )

            assert missing == ()
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_projection_reads_do_not_mutate_persisted_history():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="lineage-read-only",
            )
            organization_id = organization.id

            for sequence in (1, 2, 3):
                _persist(
                    db,
                    organization_id=organization.id,
                    observation=_observation(
                        source="source-a",
                        snapshot=f"snapshot-{sequence}",
                        asset="chunk-a",
                        asset_kind="chunk",
                        revision=f"chunk-a-r{sequence}",
                        sequence=sequence,
                    ),
                )

            db.commit()

        with SessionLocal() as db:
            before = (
                db.query(LineageObservationRecord)
                .filter(
                    LineageObservationRecord.organization_id
                    == organization_id
                )
                .count()
            )

            current = load_current_lineage_observations(
                db,
                organization_id=organization_id,
            )

            after = (
                db.query(LineageObservationRecord)
                .filter(
                    LineageObservationRecord.organization_id
                    == organization_id
                )
                .count()
            )

            assert len(current) == 1
            assert current[0].sequence == 3
            assert before == 3
            assert after == before
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)

def test_exact_revision_dependents_do_not_cross_sources():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="lineage-dependent-source",
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
                    revision="index-a-r1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "shared-chunk",
                            "shared-r1",
                            "indexed_from",
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
                    revision="index-b-r1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "shared-chunk",
                            "shared-r1",
                            "indexed_from",
                        ),
                    ),
                ),
            )
            db.commit()

        with SessionLocal() as db:
            source_a_dependents = (
                load_exact_revision_dependents(
                    db,
                    organization_id=organization_id,
                    source_external_id="source-a",
                    asset_external_id="shared-chunk",
                    revision_external_id="shared-r1",
                )
            )
            source_b_dependents = (
                load_exact_revision_dependents(
                    db,
                    organization_id=organization_id,
                    source_external_id="source-b",
                    asset_external_id="shared-chunk",
                    revision_external_id="shared-r1",
                )
            )

            assert [
                item.revision_external_id
                for item in source_a_dependents
            ] == ["index-a-r1"]

            assert [
                item.revision_external_id
                for item in source_b_dependents
            ] == ["index-b-r1"]

    finally:
        if organization_id is not None:
            _delete_organization(
                organization_id
            )


def test_batch_exact_revision_dependents_matches_single_lookup():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="projection-batch-equivalence",
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
                    asset="index-b",
                    asset_kind="index",
                    revision="index-b@v1",
                    sequence=1,
                    dependencies=(
                        _dependency(
                            "chunk-b",
                            "chunk-b@v1",
                        ),
                    ),
                ),
            )
            db.commit()

            chunk_a = LineageRevisionIdentity(
                source_external_id="source-a",
                asset_external_id="chunk-a",
                revision_external_id="chunk-a@v1",
            )
            chunk_b = LineageRevisionIdentity(
                source_external_id="source-a",
                asset_external_id="chunk-b",
                revision_external_id="chunk-b@v1",
            )

            batch = load_exact_revision_dependents_batch(
                db,
                organization_id=organization.id,
                revisions=(
                    chunk_b,
                    chunk_a,
                    chunk_a,
                ),
            )

            assert tuple(batch) == (
                chunk_a,
                chunk_b,
            )
            assert batch[chunk_a] == load_exact_revision_dependents(
                db,
                organization_id=organization.id,
                source_external_id="source-a",
                asset_external_id="chunk-a",
                revision_external_id="chunk-a@v1",
            )
            assert batch[chunk_b] == load_exact_revision_dependents(
                db,
                organization_id=organization.id,
                source_external_id="source-a",
                asset_external_id="chunk-b",
                revision_external_id="chunk-b@v1",
            )
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_batch_exact_revision_dependents_keeps_sources_independent():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="projection-batch-source",
            )
            organization_id = organization.id

            for source, index in (
                ("source-a", "index-a"),
                ("source-b", "index-b"),
            ):
                _persist(
                    db,
                    organization_id=organization.id,
                    observation=_observation(
                        source=source,
                        snapshot=f"{source}-snapshot",
                        asset=index,
                        asset_kind="index",
                        revision=f"{index}@v1",
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

            source_a = LineageRevisionIdentity(
                source_external_id="source-a",
                asset_external_id="chunk-shared",
                revision_external_id="chunk-shared@v1",
            )
            source_b = LineageRevisionIdentity(
                source_external_id="source-b",
                asset_external_id="chunk-shared",
                revision_external_id="chunk-shared@v1",
            )

            result = load_exact_revision_dependents_batch(
                db,
                organization_id=organization.id,
                revisions=(
                    source_b,
                    source_a,
                ),
            )

            assert tuple(
                observation.asset_external_id
                for observation in result[source_a]
            ) == ("index-a",)
            assert tuple(
                observation.asset_external_id
                for observation in result[source_b]
            ) == ("index-b",)
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_batch_exact_revision_dependents_respects_current_only():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="projection-batch-current",
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

            old_root = LineageRevisionIdentity(
                source_external_id="source-a",
                asset_external_id="chunk-old",
                revision_external_id="chunk-old@v1",
            )

            current = load_exact_revision_dependents_batch(
                db,
                organization_id=organization.id,
                revisions=(old_root,),
                current_only=True,
            )
            historical = load_exact_revision_dependents_batch(
                db,
                organization_id=organization.id,
                revisions=(old_root,),
                current_only=False,
            )

            assert current[old_root] == ()
            assert tuple(
                observation.revision_external_id
                for observation in historical[old_root]
            ) == ("index-a@v1",)
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_batch_exact_revision_dependents_empty_input_does_not_query():
    with SessionLocal() as db:
        result = load_exact_revision_dependents_batch(
            db,
            organization_id=uuid.uuid4(),
            revisions=(),
        )

        assert result == {}


def test_exact_current_projection_returns_requested_materialization():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="lineage-current-exact",
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
                    revision="index-a-r1",
                    sequence=1,
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
                    revision="index-a-r2",
                    sequence=2,
                ),
            )
            db.commit()

        with SessionLocal() as db:
            current = load_current_lineage_observation(
                db,
                organization_id=organization_id,
                source_external_id="source-a",
                asset_external_id="index-a",
                observation_kind="materialization",
            )

            assert current is not None
            assert current.revision_external_id == "index-a-r2"
            assert current.sequence == 2
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_exact_current_projection_keeps_observation_kinds_independent():
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="lineage-current-kind",
            )
            organization_id = organization.id

            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-materialized",
                    asset="agent-a",
                    asset_kind="agent",
                    revision="agent-materialized-r4",
                    sequence=4,
                ),
            )
            _persist(
                db,
                organization_id=organization.id,
                observation=_observation(
                    source="source-a",
                    snapshot="snapshot-consumed",
                    asset="agent-a",
                    asset_kind="agent",
                    revision="agent-consumed-r9",
                    sequence=9,
                    observation_kind="consumption",
                    dependencies=(
                        _dependency(
                            "index-a",
                            "index-a-r7",
                            "retrieves_from",
                        ),
                    ),
                ),
            )
            db.commit()

        with SessionLocal() as db:
            materialization = load_current_lineage_observation(
                db,
                organization_id=organization_id,
                source_external_id="source-a",
                asset_external_id="agent-a",
                observation_kind="materialization",
            )
            consumption = load_current_lineage_observation(
                db,
                organization_id=organization_id,
                source_external_id="source-a",
                asset_external_id="agent-a",
                observation_kind="consumption",
            )

            assert materialization is not None
            assert consumption is not None
            assert (
                materialization.revision_external_id
                == "agent-materialized-r4"
            )
            assert (
                consumption.revision_external_id
                == "agent-consumed-r9"
            )
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_exact_current_projection_is_tenant_scoped():
    organization_a_id = None
    organization_b_id = None

    try:
        with SessionLocal() as db:
            organization_a = _create_organization(
                db,
                prefix="lineage-current-exact-a",
            )
            organization_b = _create_organization(
                db,
                prefix="lineage-current-exact-b",
            )

            organization_a_id = organization_a.id
            organization_b_id = organization_b.id

            _persist(
                db,
                organization_id=organization_a.id,
                observation=_observation(
                    source="shared-source",
                    snapshot="snapshot-a",
                    asset="shared-index",
                    asset_kind="index",
                    revision="index-a-r3",
                    sequence=3,
                ),
            )
            _persist(
                db,
                organization_id=organization_b.id,
                observation=_observation(
                    source="shared-source",
                    snapshot="snapshot-b",
                    asset="shared-index",
                    asset_kind="index",
                    revision="index-b-r8",
                    sequence=8,
                ),
            )
            db.commit()

        with SessionLocal() as db:
            organization_a_current = (
                load_current_lineage_observation(
                    db,
                    organization_id=organization_a_id,
                    source_external_id="shared-source",
                    asset_external_id="shared-index",
                    observation_kind="materialization",
                )
            )

            assert organization_a_current is not None
            assert (
                organization_a_current.revision_external_id
                == "index-a-r3"
            )
    finally:
        if organization_a_id is not None:
            _delete_organization(organization_a_id)
        if organization_b_id is not None:
            _delete_organization(organization_b_id)