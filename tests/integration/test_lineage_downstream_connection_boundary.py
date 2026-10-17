from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from src.core.database import SessionLocal
from src.services.lineage_downstream_connection_service import (
    create_lineage_downstream_session,
    resolve_lineage_downstream_connection,
)
from src.services.lineage_pgvector_invalidation_service import (
    invalidate_lineage_pgvector_document_revision,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


DOWNSTREAM_DATABASE_URL = (
    "postgresql://downstream_test:downstream_test@"
    "integration_downstream_db:5432/downstream_test"
)


def test_resolved_downstream_session_executes_outside_authority_database() -> None:
    authority_db = SessionLocal()
    downstream_db = None
    downstream_engine = None

    collection_id = uuid.uuid4()
    collection_name = f"phase7e4-{uuid.uuid4().hex}"
    document_id = f"document-{uuid.uuid4().hex}"

    try:
        authority_database = authority_db.execute(
            text("SELECT current_database()")
        ).scalar_one()

        connection = resolve_lineage_downstream_connection(
            connection_ref="customer-primary",
            registry_json=(
                '{"customer-primary":'
                f'"{DOWNSTREAM_DATABASE_URL}"'
                "}"
            ),
        )

        downstream_db, downstream_engine = (
            create_lineage_downstream_session(connection)
        )

        downstream_database = downstream_db.execute(
            text("SELECT current_database()")
        ).scalar_one()

        assert authority_database != downstream_database
        assert downstream_database == "downstream_test"

        downstream_db.execute(
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
        downstream_db.execute(
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
                    'phase7e4-downstream-proof',
                    '{"chunk_revision_external_id": "index-a-r7"}'::jsonb
                )
                """
            ),
            {
                "document_id": document_id,
                "collection_id": collection_id,
            },
        )
        downstream_db.commit()

        execution = invalidate_lineage_pgvector_document_revision(
            downstream_db,
            configuration={
                "collection_name": collection_name,
                "document_id": document_id,
                "document_revision_external_id": "index-a-r7",
            },
        )

        assert execution.deleted is True

        downstream_db.commit()

        remaining = downstream_db.execute(
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

        assert remaining == 0

        assert authority_database != downstream_database
    finally:
        authority_db.rollback()
        authority_db.close()

        if downstream_db is not None:
            downstream_db.rollback()
            downstream_db.close()

        if downstream_engine is not None:
            downstream_engine.dispose()