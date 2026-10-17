from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct

from pgvector.sqlalchemy import Vector
from sqlalchemy import text
from sqlalchemy.orm import Session
from benchmarks.temporal_knowledge_integrity.materialization_event import (
    MaterializationEvent,
    RevisionReference,
)

PGVECTOR_BENCHMARK_DIMENSIONS = 3


@dataclass(frozen=True)
class PgvectorMaterializationRecord:
    vector_id: str
    chunk_asset_external_id: str
    chunk_revision_external_id: str | None
    embedding: tuple[float, ...]


@dataclass(frozen=True)
class PgvectorIndexMaterialization:
    index_external_id: str
    revision_external_id: str
    consumed_chunk_revisions: tuple[str, ...]

@dataclass(frozen=True)
class PgvectorConsumerBinding:
    consumer_external_id: str
    index_external_id: str
    index_revision_external_id: str


def create_benchmark_table(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE TEMP TABLE sde_benchmark_vectors (
                vector_id TEXT NOT NULL,
                index_external_id TEXT NOT NULL,
                index_revision_external_id TEXT NOT NULL,
                chunk_asset_external_id TEXT NOT NULL,
                chunk_revision_external_id TEXT NOT NULL,
                embedding VECTOR(3) NOT NULL,
                PRIMARY KEY (
                    index_external_id,
                    index_revision_external_id,
                    vector_id
                )
            ) ON COMMIT PRESERVE ROWS
            """
        )
    )

    db.execute(
        text(
            """
            CREATE TEMP TABLE sde_benchmark_consumers (
                consumer_external_id TEXT PRIMARY KEY,
                index_external_id TEXT NOT NULL,
                index_revision_external_id TEXT NOT NULL
            ) ON COMMIT PRESERVE ROWS
            """
        )
    )

def _materialization_matches(
    db: Session,
    *,
    index_external_id: str,
    index_revision_external_id: str,
    records: tuple[PgvectorMaterializationRecord, ...],
) -> bool:
    persisted = load_index_records(
        db,
        index_external_id=index_external_id,
        index_revision_external_id=(
            index_revision_external_id
        ),
    )

    if len(persisted) != len(records):
        return False

    expected_by_vector_id = {
        record.vector_id: record
        for record in records
    }
    persisted_by_vector_id = {
        record.vector_id: record
        for record in persisted
    }

    if (
        len(expected_by_vector_id) != len(records)
        or len(persisted_by_vector_id) != len(persisted)
    ):
        return False

    if (
        expected_by_vector_id.keys()
        != persisted_by_vector_id.keys()
    ):
        return False

    for vector_id, expected in expected_by_vector_id.items():
        actual = persisted_by_vector_id[vector_id]

        if (
            actual.chunk_asset_external_id
            != expected.chunk_asset_external_id
        ):
            return False

        if (
            actual.chunk_revision_external_id
            != expected.chunk_revision_external_id
        ):
            return False

        if (
            actual.embedding
            != _pgvector_embedding(
                expected.embedding
            )
        ):
            return False

    return True

def persist_index_materialization(
    db: Session,
    *,
    index_external_id: str,
    records: tuple[PgvectorMaterializationRecord, ...],
) -> PgvectorIndexMaterialization:
    if not records:
        raise ValueError(
            "Index materialization requires at least one vector record."
        )

    for record in records:
        if len(record.embedding) != PGVECTOR_BENCHMARK_DIMENSIONS:
            raise ValueError(
                "Vector record dimension mismatch."
            )

        if not record.chunk_revision_external_id:
            raise ValueError(
                "Exact chunk revision provenance is required "
                "before pgvector persistence."
            )

    consumed_revisions = tuple(
        record.chunk_revision_external_id
        for record in records
        if record.chunk_revision_external_id is not None
    )

    revision_external_id = _index_revision_id(
        index_external_id=index_external_id,
        consumed_chunk_revisions=consumed_revisions,
    )

    existing_records = load_index_records(
        db,
        index_external_id=index_external_id,
        index_revision_external_id=(
            revision_external_id
        ),
    )

    if existing_records:
        if _materialization_matches(
            db,
            index_external_id=index_external_id,
            index_revision_external_id=(
                revision_external_id
            ),
            records=records,
        ):
            return PgvectorIndexMaterialization(
                index_external_id=index_external_id,
                revision_external_id=revision_external_id,
                consumed_chunk_revisions=consumed_revisions,
            )

        raise ValueError(
            "Index revision already exists with a different "
            "persisted materialization."
        )

    with db.begin_nested():
        for record in records:
            db.execute(
                text(
                    """
                    INSERT INTO sde_benchmark_vectors (
                        vector_id,
                        index_external_id,
                        index_revision_external_id,
                        chunk_asset_external_id,
                        chunk_revision_external_id,
                        embedding
                    )
                    VALUES (
                        :vector_id,
                        :index_external_id,
                        :index_revision_external_id,
                        :chunk_asset_external_id,
                        :chunk_revision_external_id,
                        CAST(:embedding AS vector)
                    )
                    """
                ),
                {
                    "vector_id": record.vector_id,
                    "index_external_id": index_external_id,
                    "index_revision_external_id": (
                        revision_external_id
                    ),
                    "chunk_asset_external_id": (
                        record.chunk_asset_external_id
                    ),
                    "chunk_revision_external_id": (
                        record.chunk_revision_external_id
                    ),
                    "embedding": _vector_literal(
                        record.embedding
                    ),
                },
            )

    return PgvectorIndexMaterialization(
        index_external_id=index_external_id,
        revision_external_id=revision_external_id,
        consumed_chunk_revisions=consumed_revisions,
    )


def load_index_records(
    db: Session,
    *,
    index_external_id: str,
    index_revision_external_id: str,
) -> tuple[PgvectorMaterializationRecord, ...]:
    statement = text(
        """
        SELECT
            vector_id,
            chunk_asset_external_id,
            chunk_revision_external_id,
            embedding
        FROM sde_benchmark_vectors
        WHERE index_external_id = :index_external_id
          AND index_revision_external_id = :index_revision_external_id
        ORDER BY vector_id
        """
    ).columns(
        embedding=Vector(
            PGVECTOR_BENCHMARK_DIMENSIONS
        ),
    )

    rows = db.execute(
        statement,
        {
            "index_external_id": index_external_id,
            "index_revision_external_id": (
                index_revision_external_id
            ),
        },
    ).mappings()

    return tuple(
        PgvectorMaterializationRecord(
            vector_id=row["vector_id"],
            chunk_asset_external_id=(
                row["chunk_asset_external_id"]
            ),
            chunk_revision_external_id=(
                row["chunk_revision_external_id"]
            ),
            embedding=tuple(
                float(value)
                for value in row["embedding"]
            ),
        )
        for row in rows
    )

def bind_consumer(
    db: Session,
    *,
    consumer_external_id: str,
    index_external_id: str,
    index_revision_external_id: str,
) -> PgvectorConsumerBinding:
    exists = db.execute(
        text(
            """
            SELECT 1
            FROM sde_benchmark_vectors
            WHERE index_external_id = :index_external_id
              AND index_revision_external_id = :index_revision_external_id
            LIMIT 1
            """
        ),
        {
            "index_external_id": index_external_id,
            "index_revision_external_id": (
                index_revision_external_id
            ),
        },
    ).scalar_one_or_none()

    if exists is None:
        raise ValueError(
            "Consumer binding references an index revision "
            "that was not materialized."
        )

    db.execute(
        text(
            """
            INSERT INTO sde_benchmark_consumers (
                consumer_external_id,
                index_external_id,
                index_revision_external_id
            )
            VALUES (
                :consumer_external_id,
                :index_external_id,
                :index_revision_external_id
            )
            """
        ),
        {
            "consumer_external_id": consumer_external_id,
            "index_external_id": index_external_id,
            "index_revision_external_id": (
                index_revision_external_id
            ),
        },
    )

    return PgvectorConsumerBinding(
        consumer_external_id=consumer_external_id,
        index_external_id=index_external_id,
        index_revision_external_id=(
            index_revision_external_id
        ),
    )


def load_consumer_binding(
    db: Session,
    *,
    consumer_external_id: str,
) -> PgvectorConsumerBinding:
    row = db.execute(
        text(
            """
            SELECT
                consumer_external_id,
                index_external_id,
                index_revision_external_id
            FROM sde_benchmark_consumers
            WHERE consumer_external_id = :consumer_external_id
            """
        ),
        {
            "consumer_external_id": consumer_external_id,
        },
    ).mappings().one()

    return PgvectorConsumerBinding(
        consumer_external_id=row[
            "consumer_external_id"
        ],
        index_external_id=row[
            "index_external_id"
        ],
        index_revision_external_id=row[
            "index_revision_external_id"
        ],
    )

def materialization_events_from_persisted_state(
    db: Session,
    *,
    source_external_id: str,
    snapshot_external_id: str,
    index_external_id: str,
    index_revision_external_id: str,
    consumer_external_id: str,
    consumer_revision_external_id: str,
    sequence: int,
    consumer_critical: bool = False,
) -> tuple[MaterializationEvent, ...]:
    records = load_index_records(
        db,
        index_external_id=index_external_id,
        index_revision_external_id=(
            index_revision_external_id
        ),
    )

    if not records:
        raise ValueError(
            "Index revision has no persisted vector records."
        )

    binding = load_consumer_binding(
        db,
        consumer_external_id=consumer_external_id,
    )

    if (
        binding.index_external_id
        != index_external_id
        or binding.index_revision_external_id
        != index_revision_external_id
    ):
        raise ValueError(
            "Consumer binding does not match the requested "
            "index revision."
        )

    chunk_events = tuple(
        MaterializationEvent(
            source_external_id=source_external_id,
            snapshot_external_id=snapshot_external_id,
            asset_external_id=(
                record.chunk_asset_external_id
            ),
            asset_kind="chunk",
            revision_external_id=(
                record.chunk_revision_external_id
            ),
            sequence=sequence,
        )
        for record in records
    )

    index_event = MaterializationEvent(
        source_external_id=source_external_id,
        snapshot_external_id=snapshot_external_id,
        asset_external_id=index_external_id,
        asset_kind="index",
        revision_external_id=index_revision_external_id,
        sequence=sequence,
        dependencies=tuple(
            RevisionReference(
                asset_external_id=(
                    record.chunk_asset_external_id
                ),
                revision_external_id=(
                    record.chunk_revision_external_id
                ),
                dependency_type="indexed_from",
            )
            for record in records
        ),
    )

    consumer_event = MaterializationEvent(
        source_external_id=source_external_id,
        snapshot_external_id=snapshot_external_id,
        asset_external_id=consumer_external_id,
        asset_kind="agent",
        revision_external_id=(
            consumer_revision_external_id
        ),
        sequence=sequence,
        critical=consumer_critical,
        dependencies=(
            RevisionReference(
                asset_external_id=index_external_id,
                revision_external_id=(
                    binding.index_revision_external_id
                ),
                dependency_type="retrieves_from",
            ),
        ),
    )

    return (
        *chunk_events,
        index_event,
        consumer_event,
    )

def _index_revision_id(
    *,
    index_external_id: str,
    consumed_chunk_revisions: tuple[str, ...],
) -> str:
    payload = "\x1f".join(
        consumed_chunk_revisions
    )
    digest = hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()[:16]

    return f"{index_external_id}:{digest}"

def _pgvector_embedding(
    embedding: tuple[float, ...],
) -> tuple[float, ...]:
    return tuple(
        struct.unpack(
            "!f",
            struct.pack("!f", value),
        )[0]
        for value in embedding
    )

def _vector_literal(
    embedding: tuple[float, ...],
) -> str:
    return (
        "["
        + ",".join(
            format(value, ".17g")
            for value in embedding
        )
        + "]"
    )