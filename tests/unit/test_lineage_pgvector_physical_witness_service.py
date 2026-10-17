"""Read-only physical witness contract, exercising SQL with bounded rows.

SQLite is used solely for local SQL/contract checks; run a separate disposable
real PGVector integration gate before accepting or committing this candidate.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from src.services.lineage_pgvector_physical_witness_service import (
    MAX_WITNESS_CONTENT_BYTES,
    inspect_pgvector_chunk_physical_witness,
)

ORG = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
CONTENT = "Policy v2: 14 days."


def _reference(**changes):
    reference = dict(
        organization_id=ORG,
        source_external_id="source-a",
        index_external_id="index-a",
        collection_name="tenant-a-index",
        chunk_external_id="chunk-a",
        chunk_revision_external_id="chunk-a:r2",
        snapshot_external_id="snapshot-2",
        expected_content_sha256=hashlib.sha256(CONTENT.encode("utf-8")).hexdigest(),
    )
    reference.update(changes)
    return reference


@pytest.fixture()
def physical_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def register_octet_length(connection, _record):
        connection.create_function(
            "octet_length", 1,
            lambda value: None if value is None else len(value.encode("utf-8")),
        )

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE langchain_pg_collection (uuid TEXT, name TEXT UNIQUE, cmetadata TEXT)"))
        conn.execute(text("CREATE TABLE langchain_pg_embedding (id TEXT, collection_id TEXT, cmetadata TEXT, document TEXT)"))
        conn.execute(text("INSERT INTO langchain_pg_collection VALUES (:id,:name,:meta)"), {
            "id": "col-a", "name": "tenant-a-index",
            "meta": json.dumps({
                "sde_organization_id": str(ORG),
                "sde_source_external_id": "source-a",
                "sde_index_external_id": "index-a",
            }),
        })
        conn.execute(text("INSERT INTO langchain_pg_embedding VALUES (:id,:collection,:meta,:doc)"), {
            "id": "chunk-a", "collection": "col-a",
            "meta": json.dumps({
                "source_external_id": "source-a",
                "snapshot_external_id": "snapshot-2",
                "chunk_revision_external_id": "chunk-a:r2",
            }),
            "doc": CONTENT,
        })
    db = Session(engine)
    try:
        yield db
    finally:
        db.rollback()
        db.close()
        engine.dispose()


def _modify(db, statement, **params):
    db.execute(text(statement), params)
    db.commit()


def test_full_reference_digest_matches_exact_utf8_text(physical_db):
    result = inspect_pgvector_chunk_physical_witness(physical_db, **_reference())
    assert (result.status, result.code) == ("digest_match", "content_matches_supplied_digest")
    assert result.matches_reference_content is True


def test_missing_reference_digest_never_claims_physical_content_match(physical_db):
    result = inspect_pgvector_chunk_physical_witness(
        physical_db, **_reference(expected_content_sha256=None)
    )
    assert result.status == "identity_match_only"
    assert result.matches_reference_content is False


@pytest.mark.parametrize("changed", [
    {"organization_id": uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")},
    {"source_external_id": "source-other"},
    {"index_external_id": "index-other"},
    {"chunk_revision_external_id": "chunk-a:r1"},
    {"snapshot_external_id": "snapshot-1"},
])
def test_foreign_or_stale_scope_refused(physical_db, changed):
    result = inspect_pgvector_chunk_physical_witness(physical_db, **_reference(**changed))
    assert result.status == "identity_mismatch"
    assert not result.matches_reference_content


def test_missing_collection_never_implies_success(physical_db):
    result = inspect_pgvector_chunk_physical_witness(
        physical_db, **_reference(collection_name="missing")
    )
    assert (result.status, result.code) == ("absent", "collection_absent")


def test_missing_chunk_never_implies_success(physical_db):
    result = inspect_pgvector_chunk_physical_witness(
        physical_db, **_reference(chunk_external_id="missing")
    )
    assert (result.status, result.code) == ("absent", "chunk_absent")


def test_changed_bytes_refuse_digest_match_even_with_correct_metadata(physical_db):
    _modify(physical_db, "UPDATE langchain_pg_embedding SET document = :doc", doc="Policy v2: 7 days.")
    result = inspect_pgvector_chunk_physical_witness(physical_db, **_reference())
    assert result.status == "digest_mismatch"
    assert not result.matches_reference_content


def test_invalid_collection_metadata_is_not_authorized(physical_db):
    _modify(physical_db, "UPDATE langchain_pg_collection SET cmetadata = :meta", meta='{}')
    assert inspect_pgvector_chunk_physical_witness(physical_db, **_reference()).status == "identity_mismatch"


def test_invalid_document_metadata_is_not_authorized(physical_db):
    _modify(physical_db, "UPDATE langchain_pg_embedding SET cmetadata = :meta", meta='{}')
    assert inspect_pgvector_chunk_physical_witness(physical_db, **_reference()).status == "identity_mismatch"


def test_missing_document_is_uninspectable(physical_db):
    _modify(physical_db, "UPDATE langchain_pg_embedding SET document = NULL")
    assert inspect_pgvector_chunk_physical_witness(physical_db, **_reference()).status == "content_unavailable"


def test_oversize_document_is_not_loaded_as_evidence(physical_db):
    _modify(
        physical_db, "UPDATE langchain_pg_embedding SET document = :doc",
        doc="x" * (MAX_WITNESS_CONTENT_BYTES + 1),
    )
    assert inspect_pgvector_chunk_physical_witness(physical_db, **_reference()).status == "content_unavailable"


def test_input_digest_must_be_full_canonical_sha256(physical_db):
    for invalid in ("A" * 64, "1234", "", "z" * 64):
        with pytest.raises(ValueError, match="64 lowercase hex"):
            inspect_pgvector_chunk_physical_witness(
                physical_db, **_reference(expected_content_sha256=invalid)
            )


def test_only_select_is_executed(physical_db):
    statements = []

    @event.listens_for(physical_db.bind, "before_cursor_execute")
    def collect(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    result = inspect_pgvector_chunk_physical_witness(physical_db, **_reference())
    assert result.matches_reference_content
    assert len(statements) == 1
    assert statements[0].lstrip().upper().startswith("SELECT ")
    assert "LEFT JOIN" in statements[0]


def test_sql_input_is_bound_and_cannot_inject_collection(physical_db):
    result = inspect_pgvector_chunk_physical_witness(
        physical_db, **_reference(collection_name="tenant-a-index' OR 1=1 --")
    )
    assert result.status == "absent"


def test_multibyte_utf8_hash_uses_bytes_not_characters(physical_db):
    document = "Quy định mới – 14 ngày"
    _modify(physical_db, "UPDATE langchain_pg_embedding SET document = :doc", doc=document)
    result = inspect_pgvector_chunk_physical_witness(
        physical_db,
        **_reference(expected_content_sha256=hashlib.sha256(document.encode("utf-8")).hexdigest())
    )
    assert result.matches_reference_content
