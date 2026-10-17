from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.domain.lineage_execution_target import (
    LineageExecutionTargetBinding,
    LineageExecutionTargetIdentity,
)
from src.models.lineage_execution_target_binding import (
    LineageExecutionTargetBindingRecord,
)
from src.models.organization import Organization
from src.services.lineage_execution_target_binding_service import (
    LineageExecutionTargetBindingConflictError,
    LineageExecutionTargetBindingRevisionGapError,
    load_current_lineage_execution_target_binding,
    load_lineage_execution_target_binding_revision,
    persist_lineage_execution_target_binding,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        id=uuid.uuid4(),
        name=f"lineage-execution-target-{suffix}",
        slug=f"lineage-execution-target-{suffix}",
        billing_email=f"{suffix}@example.com",
    )
    db.add(organization)
    db.flush()
    return organization


def _identity(
    *,
    source_external_id: str = "policy-source",
    asset_external_id: str = "policy-index",
) -> LineageExecutionTargetIdentity:
    return LineageExecutionTargetIdentity(
        source_external_id=source_external_id,
        asset_external_id=asset_external_id,
    )


def _binding(
    revision: int = 1,
    *,
    provider: str = "pgvector",
    target_kind: str = "collection",
    configuration: dict[str, str] | None = None,
    identity: LineageExecutionTargetIdentity | None = None,
) -> LineageExecutionTargetBinding:
    return LineageExecutionTargetBinding(
        identity=identity or _identity(),
        binding_revision=revision,
        provider=provider,
        target_kind=target_kind,
        configuration=configuration
        or {
            "database": "knowledge",
            "collection": "support",
        },
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


def test_first_binding_revision_is_persisted() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        result = persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_binding(),
        )

        assert result.created is True
        assert result.record.binding_revision == 1
        assert result.record.provider == "pgvector"
        assert result.record.target_kind == "collection"
        assert result.record.configuration == {
            "database": "knowledge",
            "collection": "support",
        }

        db.commit()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_identical_binding_revision_replay_is_idempotent() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        binding = _binding()

        first = persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=binding,
        )
        second = persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=binding,
        )

        assert first.created is True
        assert second.created is False
        assert second.record.id == first.record.id

        assert (
            db.query(LineageExecutionTargetBindingRecord)
            .filter(
                LineageExecutionTargetBindingRecord.organization_id
                == organization.id
            )
            .count()
            == 1
        )

        db.commit()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_conflicting_binding_revision_replay_fails_closed() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        first = persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_binding(),
        )

        with pytest.raises(
            LineageExecutionTargetBindingConflictError
        ):
            persist_lineage_execution_target_binding(
                db,
                organization_id=organization.id,
                binding=_binding(
                    configuration={
                        "database": "knowledge",
                        "collection": "different",
                    },
                ),
            )

        current = load_current_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            identity=_identity(),
        )

        assert current is not None
        assert current.id == first.record.id
        assert current.configuration["collection"] == "support"

        db.rollback()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_binding_revision_advances_contiguously() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        first = persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_binding(1),
        )
        second = persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_binding(
                2,
                configuration={
                    "database": "knowledge",
                    "collection": "support-v2",
                },
            ),
        )

        assert first.created is True
        assert second.created is True
        assert second.record.binding_revision == 2

        current = load_current_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            identity=_identity(),
        )

        assert current is not None
        assert current.id == second.record.id
        assert current.binding_revision == 2
        assert current.configuration["collection"] == "support-v2"

        db.commit()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_historical_binding_revision_conflict_is_rejected() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_binding(1),
        )
        persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_binding(
                2,
                configuration={"collection": "support-v2"},
            ),
        )

        with pytest.raises(
            LineageExecutionTargetBindingConflictError
        ):
            persist_lineage_execution_target_binding(
                db,
                organization_id=organization.id,
                binding=_binding(
                    1,
                    configuration={"collection": "stale"},
                ),
            )

        current = load_current_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            identity=_identity(),
        )

        assert current is not None
        assert current.binding_revision == 2
        assert current.configuration["collection"] == "support-v2"

        db.rollback()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_binding_revision_gap_is_rejected() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_binding(1),
        )

        with pytest.raises(
            LineageExecutionTargetBindingRevisionGapError
        ):
            persist_lineage_execution_target_binding(
                db,
                organization_id=organization.id,
                binding=_binding(3),
            )

        db.rollback()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_first_binding_revision_must_be_one() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        with pytest.raises(
            LineageExecutionTargetBindingRevisionGapError
        ):
            persist_lineage_execution_target_binding(
                db,
                organization_id=organization.id,
                binding=_binding(2),
            )

        db.rollback()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_binding_identity_is_tenant_isolated() -> None:
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

        first = persist_lineage_execution_target_binding(
            db,
            organization_id=first_organization.id,
            binding=_binding(),
        )
        second = persist_lineage_execution_target_binding(
            db,
            organization_id=second_organization.id,
            binding=_binding(),
        )

        assert first.created is True
        assert second.created is True
        assert first.record.id != second.record.id

        db.commit()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_current_binding_is_scoped_by_logical_target() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        first_identity = _identity(
            asset_external_id="policy-index",
        )
        second_identity = _identity(
            asset_external_id="agent-index",
        )

        persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_binding(
                identity=first_identity,
                configuration={"collection": "policy"},
            ),
        )
        persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_binding(
                identity=second_identity,
                configuration={"collection": "agent"},
            ),
        )

        first = load_current_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            identity=first_identity,
        )
        second = load_current_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            identity=second_identity,
        )

        assert first is not None
        assert second is not None
        assert first.configuration["collection"] == "policy"
        assert second.configuration["collection"] == "agent"

        db.commit()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_exact_historical_binding_revision_can_be_loaded() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_binding(
                1,
                configuration={"collection": "support-v1"},
            ),
        )
        persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_binding(
                2,
                configuration={"collection": "support-v2"},
            ),
        )

        historical = load_lineage_execution_target_binding_revision(
            db,
            organization_id=organization.id,
            identity=_identity(),
            binding_revision=1,
        )
        current = load_current_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            identity=_identity(),
        )

        assert historical is not None
        assert historical.binding_revision == 1
        assert historical.configuration["collection"] == "support-v1"

        assert current is not None
        assert current.binding_revision == 2
        assert current.configuration["collection"] == "support-v2"

        db.commit()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_binding_persistence_rolls_back_with_caller_transaction() -> None:
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        result = persist_lineage_execution_target_binding(
            execution_db,
            organization_id=organization.id,
            binding=_binding(),
        )

        record_id = result.record.id
        execution_db.rollback()

        persisted = (
            verify_db.query(LineageExecutionTargetBindingRecord)
            .filter(
                LineageExecutionTargetBindingRecord.id == record_id
            )
            .one_or_none()
        )

        assert persisted is None
    finally:
        setup_db.close()
        execution_db.close()
        verify_db.close()
        _cleanup(organization_ids)


def test_concurrent_identical_binding_revision_converges() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        organization_id = organization.id
        binding = _binding()
        barrier = threading.Barrier(2)
        results: list[tuple[bool, uuid.UUID]] = []
        errors: list[BaseException] = []
        result_lock = threading.Lock()

        def publish() -> None:
            db = SessionLocal()

            try:
                barrier.wait()
                result = persist_lineage_execution_target_binding(
                    db,
                    organization_id=organization_id,
                    binding=binding,
                )
                db.commit()

                with result_lock:
                    results.append(
                        (
                            result.created,
                            result.record.id,
                        )
                    )
            except BaseException as exc:
                db.rollback()

                with result_lock:
                    errors.append(exc)
            finally:
                db.close()

        threads = [
            threading.Thread(target=publish),
            threading.Thread(target=publish),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()

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
                verify_db.query(LineageExecutionTargetBindingRecord)
                .filter(
                    LineageExecutionTargetBindingRecord.organization_id
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


def test_concurrent_conflicting_binding_revision_fails_closed() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        organization_id = organization.id
        barrier = threading.Barrier(2)
        successes: list[tuple[uuid.UUID, str]] = []
        conflicts: list[
            LineageExecutionTargetBindingConflictError
        ] = []
        errors: list[BaseException] = []
        result_lock = threading.Lock()

        def publish(collection: str) -> None:
            db = SessionLocal()

            try:
                barrier.wait()
                result = persist_lineage_execution_target_binding(
                    db,
                    organization_id=organization_id,
                    binding=_binding(
                        configuration={
                            "collection": collection,
                        },
                    ),
                )
                db.commit()

                with result_lock:
                    successes.append(
                        (
                            result.record.id,
                            collection,
                        )
                    )
            except LineageExecutionTargetBindingConflictError as exc:
                db.rollback()

                with result_lock:
                    conflicts.append(exc)
            except BaseException as exc:
                db.rollback()

                with result_lock:
                    errors.append(exc)
            finally:
                db.close()

        threads = [
            threading.Thread(
                target=publish,
                args=("support-a",),
            ),
            threading.Thread(
                target=publish,
                args=("support-b",),
            ),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()

        assert errors == []
        assert len(successes) == 1
        assert len(conflicts) == 1

        verify_db = SessionLocal()

        try:
            rows = (
                verify_db.query(LineageExecutionTargetBindingRecord)
                .filter(
                    LineageExecutionTargetBindingRecord.organization_id
                    == organization_id
                )
                .all()
            )

            assert len(rows) == 1
            assert (
                rows[0].configuration["collection"]
                == successes[0][1]
            )
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_concurrent_next_revision_serializes_after_current() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        organization_id = organization.id

        persist_lineage_execution_target_binding(
            setup_db,
            organization_id=organization_id,
            binding=_binding(1),
        )
        setup_db.commit()

        barrier = threading.Barrier(2)
        results: list[tuple[bool, uuid.UUID]] = []
        errors: list[BaseException] = []
        result_lock = threading.Lock()

        def publish_revision_two() -> None:
            db = SessionLocal()

            try:
                barrier.wait()
                result = persist_lineage_execution_target_binding(
                    db,
                    organization_id=organization_id,
                    binding=_binding(
                        2,
                        configuration={
                            "collection": "support-v2",
                        },
                    ),
                )
                db.commit()

                with result_lock:
                    results.append(
                        (
                            result.created,
                            result.record.id,
                        )
                    )
            except BaseException as exc:
                db.rollback()

                with result_lock:
                    errors.append(exc)
            finally:
                db.close()

        threads = [
            threading.Thread(target=publish_revision_two),
            threading.Thread(target=publish_revision_two),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()

        assert errors == []
        assert len(results) == 2
        assert sorted(created for created, _ in results) == [
            False,
            True,
        ]
        assert len({record_id for _, record_id in results}) == 1

        verify_db = SessionLocal()

        try:
            rows = (
                verify_db.query(LineageExecutionTargetBindingRecord)
                .filter(
                    LineageExecutionTargetBindingRecord.organization_id
                    == organization_id
                )
                .order_by(
                    LineageExecutionTargetBindingRecord.binding_revision.asc()
                )
                .all()
            )

            assert [row.binding_revision for row in rows] == [1, 2]
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_binding_conflict_preserves_caller_transaction() -> None:
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        persist_lineage_execution_target_binding(
            setup_db,
            organization_id=organization.id,
            binding=_binding(),
        )
        setup_db.commit()

        independent_identity = _identity(
            asset_external_id="independent-index",
        )
        independent = persist_lineage_execution_target_binding(
            execution_db,
            organization_id=organization.id,
            binding=_binding(
                identity=independent_identity,
                configuration={
                    "collection": "independent",
                },
            ),
        )

        with pytest.raises(
            LineageExecutionTargetBindingConflictError
        ):
            persist_lineage_execution_target_binding(
                execution_db,
                organization_id=organization.id,
                binding=_binding(
                    configuration={
                        "collection": "conflict",
                    },
                ),
            )

        independent_id = independent.record.id
        execution_db.commit()

        persisted = (
            verify_db.query(LineageExecutionTargetBindingRecord)
            .filter(
                LineageExecutionTargetBindingRecord.id
                == independent_id
            )
            .one_or_none()
        )

        assert persisted is not None
        assert persisted.configuration["collection"] == "independent"
    finally:
        setup_db.close()
        execution_db.close()
        verify_db.close()
        _cleanup(organization_ids)