from __future__ import annotations

import json
import os
import uuid

import pytest
from sqlalchemy import text

from src.core.database import SessionLocal
from src.services.lineage_pgvector_invalidation_service import (
    LineagePgVectorInvalidationConfigurationError,
    LineagePgVectorInvalidationRevisionConflictError,
    invalidate_lineage_pgvector_document_revision,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _collection_name() -> str:
    return f"sde-execution-{uuid.uuid4().hex}"


def _insert_collection(
    db,
    *,
    collection_id: uuid.UUID,
    collection_name: str,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO langchain_pg_collection (
                uuid,
                name,
                cmetadata
            )
            VALUES (
                :collection_id,
                :collection_name,
                '{}'::json
            )
            """
        ),
        {
            "collection_id": collection_id,
            "collection_name": collection_name,
        },
    )


def _insert_document(
    db,
    *,
    collection_id: uuid.UUID,
    document_id: str,
    document_revision_external_id: str | None = None,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO langchain_pg_embedding (
                id,
                collection_id,
                embedding,
                document,
                cmetadata
            )
            VALUES (
                :document_id,
                :collection_id,
                '[0,0,0,0,0,0,0,0]'::vector,
                'execution-test',
                CAST(:cmetadata AS jsonb)
            )
            """
        ),
        {
            "document_id": document_id,
            "collection_id": collection_id,
            "cmetadata": json.dumps(
                {}
                if document_revision_external_id is None
                else {
                    "chunk_revision_external_id": (
                        document_revision_external_id
                    )
                }
            ),
        },
    )


def _document_exists(
    db,
    *,
    collection_name: str,
    document_id: str,
) -> bool:
    count = db.execute(
        text(
            """
            SELECT count(*)
            FROM langchain_pg_embedding AS embedding
            JOIN langchain_pg_collection AS collection
              ON collection.uuid = embedding.collection_id
            WHERE collection.name = :collection_name
              AND embedding.id = :document_id
            """
        ),
        {
            "collection_name": collection_name,
            "document_id": document_id,
        },
    ).scalar_one()

    return count == 1


def _configuration(
    *,
    collection_name: str,
    document_id: str,
) -> dict[str, str]:
    return {
        "collection_name": collection_name,
        "document_id": document_id,
    }


def test_exact_document_revision_is_invalidated() -> None:
    db = SessionLocal()

    try:
        collection_id = uuid.uuid4()
        collection_name = _collection_name()
        document_id = f"document-{uuid.uuid4().hex}"
        revision_external_id = f"{document_id}@v1"

        _insert_collection(
            db,
            collection_id=collection_id,
            collection_name=collection_name,
        )
        _insert_document(
            db,
            collection_id=collection_id,
            document_id=document_id,
            document_revision_external_id=revision_external_id,
        )

        result = invalidate_lineage_pgvector_document_revision(
            db,
            configuration={
                "collection_name": collection_name,
                "document_id": document_id,
                "document_revision_external_id": revision_external_id,
            },
        )

        assert result.deleted is True
        assert not _document_exists(
            db,
            collection_name=collection_name,
            document_id=document_id,
        )
    finally:
        db.rollback()
        db.close()


def test_document_revision_invalidation_is_idempotent_when_absent() -> None:
    db = SessionLocal()

    try:
        result = invalidate_lineage_pgvector_document_revision(
            db,
            configuration={
                "collection_name": _collection_name(),
                "document_id": f"document-{uuid.uuid4().hex}",
                "document_revision_external_id": "document@v1",
            },
        )

        assert result.deleted is False
    finally:
        db.rollback()
        db.close()


def test_document_revision_mismatch_fails_closed() -> None:
    db = SessionLocal()

    try:
        collection_id = uuid.uuid4()
        collection_name = _collection_name()
        document_id = f"document-{uuid.uuid4().hex}"

        _insert_collection(
            db,
            collection_id=collection_id,
            collection_name=collection_name,
        )
        _insert_document(
            db,
            collection_id=collection_id,
            document_id=document_id,
            document_revision_external_id=f"{document_id}@v2",
        )

        with pytest.raises(
            LineagePgVectorInvalidationRevisionConflictError
        ):
            invalidate_lineage_pgvector_document_revision(
                db,
                configuration={
                    "collection_name": collection_name,
                    "document_id": document_id,
                    "document_revision_external_id": f"{document_id}@v1",
                },
            )

        assert _document_exists(
            db,
            collection_name=collection_name,
            document_id=document_id,
        )
    finally:
        db.rollback()
        db.close()


def test_document_revision_missing_metadata_fails_closed() -> None:
    db = SessionLocal()

    try:
        collection_id = uuid.uuid4()
        collection_name = _collection_name()
        document_id = f"document-{uuid.uuid4().hex}"

        _insert_collection(
            db,
            collection_id=collection_id,
            collection_name=collection_name,
        )
        _insert_document(
            db,
            collection_id=collection_id,
            document_id=document_id,
        )

        with pytest.raises(
            LineagePgVectorInvalidationRevisionConflictError
        ):
            invalidate_lineage_pgvector_document_revision(
                db,
                configuration={
                    "collection_name": collection_name,
                    "document_id": document_id,
                    "document_revision_external_id": (
                        f"{document_id}@v1"
                    ),
                },
            )

        assert _document_exists(
            db,
            collection_name=collection_name,
            document_id=document_id,
        )
    finally:
        db.rollback()
        db.close()
