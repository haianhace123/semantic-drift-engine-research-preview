from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.models.lineage_observation import (
    LineageObservationDependencyRecord,
    LineageObservationRecord,
)
from src.models.organization import Organization
from src.services.lineage_observation_persistence_service import (
    LineageObservationConflictError,
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
        name=f"lineage-observation-{suffix}",
        slug=f"lineage-observation-{suffix}",
        billing_email=f"{suffix}@example.com",
    )
    db.add(organization)
    db.flush()
    return organization


def _observation(
    *,
    sequence: int = 1,
    snapshot_external_id: str = "snapshot-v1",
    revision_external_id: str = "index-v1",
    dependencies: tuple[LineageRevisionReference, ...] = (),
) -> LineageObservation:
    return LineageObservation(
        observation_kind="materialization",
        source_external_id="policy-source",
        snapshot_external_id=snapshot_external_id,
        asset_external_id="policy-index",
        asset_kind="index",
        revision_external_id=revision_external_id,
        sequence=sequence,
        dependencies=dependencies,
    )


def _dependency(
    asset: str,
    revision: str,
) -> LineageRevisionReference:
    return LineageRevisionReference(
        asset_external_id=asset,
        revision_external_id=revision,
        dependency_type="indexed_from",
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


def test_lineage_observation_persists_exact_dependencies() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        result = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_observation(
                dependencies=(
                    _dependency("chunk-b", "chunk-b-v2"),
                    _dependency("chunk-a", "chunk-a-v1"),
                ),
            ),
        )

        assert result.created is True
        record_id = result.record.id
        db.commit()

        db.expire_all()

        persisted = (
            db.query(LineageObservationRecord)
            .filter(
                LineageObservationRecord.id == record_id
            )
            .one()
        )

        assert persisted.organization_id == organization.id
        assert persisted.snapshot_external_id == "snapshot-v1"
        assert persisted.revision_external_id == "index-v1"

        dependencies = {
            (
                dependency.asset_external_id,
                dependency.revision_external_id,
                dependency.dependency_type,
            )
            for dependency in persisted.dependencies
        }

        assert dependencies == {
            ("chunk-a", "chunk-a-v1", "indexed_from"),
            ("chunk-b", "chunk-b-v2", "indexed_from"),
        }
    finally:
        db.close()
        _cleanup(organization_ids)


def test_identical_replay_is_idempotent() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        observation = _observation(
            dependencies=(
                _dependency("chunk-a", "chunk-a-v1"),
            ),
        )

        first = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=observation,
        )
        second = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=observation,
        )

        assert first.created is True
        assert second.created is False
        assert second.record.id == first.record.id

        assert (
            db.query(LineageObservationRecord)
            .filter(
                LineageObservationRecord.organization_id
                == organization.id
            )
            .count()
            == 1
        )

        db.commit()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_dependency_order_does_not_change_replay_identity() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        first = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_observation(
                dependencies=(
                    _dependency("chunk-a", "chunk-a-v1"),
                    _dependency("chunk-b", "chunk-b-v1"),
                ),
            ),
        )
        second = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_observation(
                dependencies=(
                    _dependency("chunk-b", "chunk-b-v1"),
                    _dependency("chunk-a", "chunk-a-v1"),
                ),
            ),
        )

        assert first.created is True
        assert second.created is False
        assert second.record.id == first.record.id

        db.commit()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_conflicting_replay_fails_closed() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_observation(),
        )

        with pytest.raises(
            LineageObservationConflictError
        ):
            persist_lineage_observation(
                db,
                organization_id=organization.id,
                observation=_observation(
                    revision_external_id="index-conflict",
                ),
            )

        db.rollback()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_same_external_identity_is_tenant_isolated() -> None:
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

        first = persist_lineage_observation(
            db,
            organization_id=first_organization.id,
            observation=_observation(),
        )
        second = persist_lineage_observation(
            db,
            organization_id=second_organization.id,
            observation=_observation(),
        )

        assert first.record.id != second.record.id
        assert first.created is True
        assert second.created is True

        db.commit()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_out_of_order_observations_are_preserved() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        for sequence in (3, 1, 2):
            persist_lineage_observation(
                db,
                organization_id=organization.id,
                observation=_observation(
                    sequence=sequence,
                    snapshot_external_id=f"snapshot-v{sequence}",
                    revision_external_id=f"index-v{sequence}",
                ),
            )

        db.commit()

        rows = (
            db.query(LineageObservationRecord)
            .filter(
                LineageObservationRecord.organization_id
                == organization.id
            )
            .order_by(LineageObservationRecord.sequence.asc())
            .all()
        )

        assert [row.sequence for row in rows] == [1, 2, 3]
        assert [row.revision_external_id for row in rows] == [
            "index-v1",
            "index-v2",
            "index-v3",
        ]
    finally:
        db.close()
        _cleanup(organization_ids)


def test_lineage_persistence_rolls_back_with_caller_transaction() -> None:
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        result = persist_lineage_observation(
            execution_db,
            organization_id=organization.id,
            observation=_observation(),
        )

        record_id = result.record.id
        execution_db.rollback()

        persisted = (
            verify_db.query(LineageObservationRecord)
            .filter(
                LineageObservationRecord.id == record_id
            )
            .one_or_none()
        )

        assert persisted is None
    finally:
        setup_db.close()
        execution_db.close()
        verify_db.close()
        _cleanup(organization_ids)


def test_parent_and_dependencies_commit_atomically() -> None:
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        result = persist_lineage_observation(
            execution_db,
            organization_id=organization.id,
            observation=_observation(
                dependencies=(
                    _dependency("chunk-a", "chunk-a-v1"),
                    _dependency("chunk-b", "chunk-b-v1"),
                ),
            ),
        )

        record_id = result.record.id
        execution_db.rollback()

        assert (
            verify_db.query(LineageObservationRecord)
            .filter(
                LineageObservationRecord.id == record_id
            )
            .one_or_none()
            is None
        )
        assert (
            verify_db.query(LineageObservationDependencyRecord)
            .filter(
                LineageObservationDependencyRecord.observation_id
                == record_id
            )
            .count()
            == 0
        )
    finally:
        setup_db.close()
        execution_db.close()
        verify_db.close()
        _cleanup(organization_ids)


def test_concurrent_identical_replay_creates_one_observation() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        organization_id = organization.id
        observation = _observation(
            dependencies=(
                _dependency("chunk-a", "chunk-a-v1"),
            ),
        )

        barrier = threading.Barrier(2)
        results: list[tuple[bool, uuid.UUID]] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def ingest() -> None:
            db = SessionLocal()

            try:
                barrier.wait()
                result = persist_lineage_observation(
                    db,
                    organization_id=organization_id,
                    observation=observation,
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
            threading.Thread(target=ingest),
            threading.Thread(target=ingest),
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
        assert sorted(created for created, _ in results) == [
            False,
            True,
        ]
        assert len({record_id for _, record_id in results}) == 1

        verify_db = SessionLocal()

        try:
            assert (
                verify_db.query(LineageObservationRecord)
                .filter(
                    LineageObservationRecord.organization_id
                    == organization_id
                )
                .count()
                == 1
            )
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_concurrent_conflicting_replay_fails_closed() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        organization_id = organization.id
        barrier = threading.Barrier(2)
        successes: list[tuple[bool, uuid.UUID, str]] = []
        conflicts: list[LineageObservationConflictError] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def ingest(
            revision_external_id: str,
        ) -> None:
            db = SessionLocal()

            try:
                barrier.wait()
                result = persist_lineage_observation(
                    db,
                    organization_id=organization_id,
                    observation=_observation(
                        revision_external_id=revision_external_id,
                    ),
                )
                db.commit()

                with lock:
                    successes.append(
                        (
                            result.created,
                            result.record.id,
                            revision_external_id,
                        )
                    )
            except LineageObservationConflictError as exc:
                db.rollback()

                with lock:
                    conflicts.append(exc)
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                db.close()

        threads = [
            threading.Thread(
                target=ingest,
                args=("index-v1",),
            ),
            threading.Thread(
                target=ingest,
                args=("index-conflict",),
            ),
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
        assert len(successes) == 1
        assert successes[0][0] is True
        assert len(conflicts) == 1

        verify_db = SessionLocal()

        try:
            rows = (
                verify_db.query(LineageObservationRecord)
                .filter(
                    LineageObservationRecord.organization_id
                    == organization_id
                )
                .all()
            )

            assert len(rows) == 1
            assert rows[0].revision_external_id in {
                "index-v1",
                "index-conflict",
            }
            assert (
                rows[0].revision_external_id
                == successes[0][2]
            )
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_conflicting_replay_preserves_outer_transaction() -> None:
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        persist_lineage_observation(
            setup_db,
            organization_id=organization.id,
            observation=_observation(),
        )
        setup_db.commit()

        independent = persist_lineage_observation(
            execution_db,
            organization_id=organization.id,
            observation=LineageObservation(
                observation_kind="materialization",
                source_external_id="policy-source",
                snapshot_external_id="snapshot-independent",
                asset_external_id="independent-index",
                asset_kind="index",
                revision_external_id="independent-index-v1",
                sequence=1,
            ),
        )

        with pytest.raises(
            LineageObservationConflictError
        ):
            persist_lineage_observation(
                execution_db,
                organization_id=organization.id,
                observation=_observation(
                    revision_external_id="index-conflict",
                ),
            )

        independent_id = independent.record.id
        execution_db.commit()

        persisted = (
            verify_db.query(LineageObservationRecord)
            .filter(
                LineageObservationRecord.id == independent_id
            )
            .one_or_none()
        )

        assert persisted is not None
        assert (
            persisted.revision_external_id
            == "independent-index-v1"
        )
    finally:
        setup_db.close()
        execution_db.close()
        verify_db.close()
        _cleanup(organization_ids)