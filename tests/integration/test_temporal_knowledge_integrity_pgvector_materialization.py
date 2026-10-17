import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from benchmarks.temporal_knowledge_integrity.pgvector_materialization import (
    PgvectorMaterializationRecord,
    create_benchmark_table,
    load_index_records,
    persist_index_materialization,
    bind_consumer,
    load_consumer_binding,
    materialization_events_from_persisted_state,
)
from src.core.database import SessionLocal
from benchmarks.temporal_knowledge_integrity.materialization_event import (
    build_lineage_from_projection,
    project_materialization_events,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="DB integration tests are disabled.",
)


def test_pgvector_boundary_preserves_exact_chunk_revision_provenance() -> None:
    db = SessionLocal()

    try:
        create_benchmark_table(db)

        materialization = persist_index_materialization(
            db,
            index_external_id="support-index",
            records=(
                PgvectorMaterializationRecord(
                    vector_id="vector-001",
                    chunk_asset_external_id=(
                        "policy:refund-rule"
                    ),
                    chunk_revision_external_id=(
                        "policy:refund-rule:revision-v1"
                    ),
                    embedding=(0.1, 0.2, 0.3),
                ),
                PgvectorMaterializationRecord(
                    vector_id="vector-002",
                    chunk_asset_external_id=(
                        "policy:shipping-rule"
                    ),
                    chunk_revision_external_id=(
                        "policy:shipping-rule:revision-v1"
                    ),
                    embedding=(0.4, 0.5, 0.6),
                ),
            ),
        )

        persisted = load_index_records(
            db,
            index_external_id="support-index",
            index_revision_external_id=(
                materialization.revision_external_id
            ),
        )

        assert tuple(
            record.chunk_revision_external_id
            for record in persisted
        ) == (
            "policy:refund-rule:revision-v1",
            "policy:shipping-rule:revision-v1",
        )

        assert (
            materialization.consumed_chunk_revisions
            == (
                "policy:refund-rule:revision-v1",
                "policy:shipping-rule:revision-v1",
            )
        )
        assert materialization.revision_external_id.startswith(
            "support-index:"
        )
    finally:
        db.rollback()
        db.close()


def test_pgvector_boundary_fails_closed_without_exact_revision_provenance() -> None:
    db = SessionLocal()

    try:
        create_benchmark_table(db)

        with pytest.raises(
            ValueError,
            match=(
                "Exact chunk revision provenance is required "
                "before pgvector persistence"
            ),
        ):
            persist_index_materialization(
                db,
                index_external_id="support-index",
                records=(
                    PgvectorMaterializationRecord(
                        vector_id="vector-001",
                        chunk_asset_external_id=(
                            "policy:refund-rule"
                        ),
                        chunk_revision_external_id=None,
                        embedding=(0.1, 0.2, 0.3),
                    ),
                ),
            )
    finally:
        db.rollback()
        db.close()

def test_consumer_remains_bound_to_exact_historical_index_revision() -> None:
    db = SessionLocal()

    try:
        create_benchmark_table(db)

        version_one = persist_index_materialization(
            db,
            index_external_id="support-index",
            records=(
                PgvectorMaterializationRecord(
                    vector_id="refund-vector",
                    chunk_asset_external_id=(
                        "policy:refund-rule"
                    ),
                    chunk_revision_external_id=(
                        "policy:refund-rule:revision-v1"
                    ),
                    embedding=(0.1, 0.2, 0.3),
                ),
            ),
        )

        binding = bind_consumer(
            db,
            consumer_external_id="support-agent",
            index_external_id="support-index",
            index_revision_external_id=(
                version_one.revision_external_id
            ),
        )

        version_two = persist_index_materialization(
            db,
            index_external_id="support-index",
            records=(
                PgvectorMaterializationRecord(
                    vector_id="refund-vector",
                    chunk_asset_external_id=(
                        "policy:refund-rule"
                    ),
                    chunk_revision_external_id=(
                        "policy:refund-rule:revision-v2"
                    ),
                    embedding=(0.4, 0.5, 0.6),
                ),
            ),
        )

        persisted_binding = load_consumer_binding(
            db,
            consumer_external_id="support-agent",
        )

        assert (
            version_one.revision_external_id
            != version_two.revision_external_id
        )
        assert (
            binding.index_revision_external_id
            == version_one.revision_external_id
        )
        assert (
            persisted_binding.index_revision_external_id
            == version_one.revision_external_id
        )

        version_one_records = load_index_records(
            db,
            index_external_id="support-index",
            index_revision_external_id=(
                version_one.revision_external_id
            ),
        )
        version_two_records = load_index_records(
            db,
            index_external_id="support-index",
            index_revision_external_id=(
                version_two.revision_external_id
            ),
        )

        assert (
            version_one_records[0].chunk_revision_external_id
            == "policy:refund-rule:revision-v1"
        )
        assert (
            version_two_records[0].chunk_revision_external_id
            == "policy:refund-rule:revision-v2"
        )
    finally:
        db.rollback()
        db.close()


def test_consumer_binding_rejects_unmaterialized_index_revision() -> None:
    db = SessionLocal()

    try:
        create_benchmark_table(db)

        with pytest.raises(
            ValueError,
            match=(
                "Consumer binding references an index revision "
                "that was not materialized"
            ),
        ):
            bind_consumer(
                db,
                consumer_external_id="support-agent",
                index_external_id="support-index",
                index_revision_external_id=(
                    "support-index:unobserved"
                ),
            )
    finally:
        db.rollback()
        db.close()

def test_persisted_mixed_version_binding_reconstructs_exact_causal_lineage(
) -> None:
    db = SessionLocal()

    try:
        create_benchmark_table(db)

        version_one = persist_index_materialization(
            db,
            index_external_id="support-index",
            records=(
                PgvectorMaterializationRecord(
                    vector_id="vector-001",
                    chunk_asset_external_id=(
                        "policy:refund-rule"
                    ),
                    chunk_revision_external_id=(
                        "policy:refund-rule:revision-v1"
                    ),
                    embedding=(0.1, 0.2, 0.3),
                ),
            ),
        )

        bind_consumer(
            db,
            consumer_external_id="support-agent",
            index_external_id="support-index",
            index_revision_external_id=(
                version_one.revision_external_id
            ),
        )

        version_two = persist_index_materialization(
            db,
            index_external_id="support-index",
            records=(
                PgvectorMaterializationRecord(
                    vector_id="vector-001",
                    chunk_asset_external_id=(
                        "policy:refund-rule"
                    ),
                    chunk_revision_external_id=(
                        "policy:refund-rule:revision-v2"
                    ),
                    embedding=(0.4, 0.5, 0.6),
                ),
            ),
        )

        assert (
            version_two.revision_external_id
            != version_one.revision_external_id
        )

        events = materialization_events_from_persisted_state(
            db,
            source_external_id="policy-source",
            snapshot_external_id="snapshot-v2",
            index_external_id="support-index",
            index_revision_external_id=(
                version_one.revision_external_id
            ),
            consumer_external_id="support-agent",
            consumer_revision_external_id=(
                "support-agent:revision-v1"
            ),
            sequence=2,
            consumer_critical=True,
        )

        projection = project_materialization_events(
            events,
            current_snapshot_external_id="snapshot-v2",
        )

        lineage = build_lineage_from_projection(
            projection,
            content_hash="content-hash-v2",
        )

        consumer_dependencies = tuple(
            dependency
            for dependency in lineage.revision_dependencies
            if (
                dependency.downstream_asset_external_id
                == "support-agent"
            )
        )

        assert len(consumer_dependencies) == 1

        dependency = consumer_dependencies[0]

        assert (
            dependency.upstream_asset_external_id
            == "support-index"
        )
        assert (
            dependency.upstream_revision_external_id
            == version_one.revision_external_id
        )
        assert (
            dependency.upstream_revision_external_id
            != version_two.revision_external_id
        )
        assert (
            dependency.downstream_revision_external_id
            == "support-agent:revision-v1"
        )
        assert dependency.dependency_type == "retrieves_from"
    finally:
        db.rollback()
        db.close()

def test_failed_index_materialization_leaves_no_partial_revision() -> None:
    db = SessionLocal()

    try:
        create_benchmark_table(db)

        with pytest.raises(IntegrityError):
            persist_index_materialization(
                db,
                index_external_id="support-index",
                records=(
                    PgvectorMaterializationRecord(
                        vector_id="duplicate-vector",
                        chunk_asset_external_id=(
                            "policy:refund-rule"
                        ),
                        chunk_revision_external_id=(
                            "policy:refund-rule:revision-v1"
                        ),
                        embedding=(0.1, 0.2, 0.3),
                    ),
                    PgvectorMaterializationRecord(
                        vector_id="duplicate-vector",
                        chunk_asset_external_id=(
                            "policy:shipping-rule"
                        ),
                        chunk_revision_external_id=(
                            "policy:shipping-rule:revision-v1"
                        ),
                        embedding=(0.4, 0.5, 0.6),
                    ),
                ),
            )

        persisted_row_count = db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM sde_benchmark_vectors
                WHERE index_external_id = :index_external_id
                """
            ),
            {
                "index_external_id": "support-index",
            },
        ).scalar_one()

        assert persisted_row_count == 0

        successful_retry = persist_index_materialization(
            db,
            index_external_id="support-index",
            records=(
                PgvectorMaterializationRecord(
                    vector_id="refund-vector",
                    chunk_asset_external_id=(
                        "policy:refund-rule"
                    ),
                    chunk_revision_external_id=(
                        "policy:refund-rule:revision-v1"
                    ),
                    embedding=(0.1, 0.2, 0.3),
                ),
                PgvectorMaterializationRecord(
                    vector_id="shipping-vector",
                    chunk_asset_external_id=(
                        "policy:shipping-rule"
                    ),
                    chunk_revision_external_id=(
                        "policy:shipping-rule:revision-v1"
                    ),
                    embedding=(0.4, 0.5, 0.6),
                ),
            ),
        )

        persisted_retry = load_index_records(
            db,
            index_external_id="support-index",
            index_revision_external_id=(
                successful_retry.revision_external_id
            ),
        )

        assert len(persisted_retry) == 2
    finally:
        db.rollback()
        db.close()

def test_identical_index_materialization_retry_is_idempotent() -> None:
    db = SessionLocal()

    try:
        create_benchmark_table(db)

        records = (
            PgvectorMaterializationRecord(
                vector_id="refund-vector",
                chunk_asset_external_id=(
                    "policy:refund-rule"
                ),
                chunk_revision_external_id=(
                    "policy:refund-rule:revision-v1"
                ),
                embedding=(0.1, 0.2, 0.3),
            ),
            PgvectorMaterializationRecord(
                vector_id="shipping-vector",
                chunk_asset_external_id=(
                    "policy:shipping-rule"
                ),
                chunk_revision_external_id=(
                    "policy:shipping-rule:revision-v1"
                ),
                embedding=(0.4, 0.5, 0.6),
            ),
        )

        first = persist_index_materialization(
            db,
            index_external_id="support-index",
            records=records,
        )
        second = persist_index_materialization(
            db,
            index_external_id="support-index",
            records=records,
        )

        assert second == first

        persisted = load_index_records(
            db,
            index_external_id="support-index",
            index_revision_external_id=(
                first.revision_external_id
            ),
        )

        assert len(persisted) == 2
    finally:
        db.rollback()
        db.close()

def test_conflicting_index_materialization_retry_fails_closed() -> None:
    db = SessionLocal()

    try:
        create_benchmark_table(db)

        original = persist_index_materialization(
            db,
            index_external_id="support-index",
            records=(
                PgvectorMaterializationRecord(
                    vector_id="refund-vector",
                    chunk_asset_external_id=(
                        "policy:refund-rule"
                    ),
                    chunk_revision_external_id=(
                        "policy:refund-rule:revision-v1"
                    ),
                    embedding=(0.1, 0.2, 0.3),
                ),
            ),
        )

        with pytest.raises(
            ValueError,
            match=(
                "Index revision already exists with a different "
                "persisted materialization"
            ),
        ):
            persist_index_materialization(
                db,
                index_external_id="support-index",
                records=(
                    PgvectorMaterializationRecord(
                        vector_id="different-vector",
                        chunk_asset_external_id=(
                            "policy:refund-rule"
                        ),
                        chunk_revision_external_id=(
                            "policy:refund-rule:revision-v1"
                        ),
                        embedding=(0.9, 0.8, 0.7),
                    ),
                ),
            )

        persisted = load_index_records(
            db,
            index_external_id="support-index",
            index_revision_external_id=(
                original.revision_external_id
            ),
        )

        assert len(persisted) == 1
        assert persisted[0].vector_id == "refund-vector"
        assert persisted[0].embedding == pytest.approx(
            (0.1, 0.2, 0.3)
        )
    finally:
        db.rollback()
        db.close()