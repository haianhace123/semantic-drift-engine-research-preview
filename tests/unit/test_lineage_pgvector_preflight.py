"""Read-only contract tests without requiring a running PostgreSQL instance."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.domain.lineage_pgvector_collection import sde_pgvector_collection_metadata
from src.services.lineage_pgvector_preflight_service import inspect_pgvector_causal_target


@pytest.fixture
def prepared_database():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    org = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE langchain_pg_collection (uuid TEXT PRIMARY KEY, name TEXT, cmetadata TEXT)"))
        conn.execute(text("CREATE TABLE langchain_pg_embedding (id TEXT PRIMARY KEY, collection_id TEXT, cmetadata TEXT)"))
        conn.execute(
            text("INSERT INTO langchain_pg_collection VALUES (:id, 'policy-index-physical', :metadata)"),
            {"id": "collection-1", "metadata": json.dumps(sde_pgvector_collection_metadata(
                organization_id=org, source_external_id="source-a", index_external_id="index-a"
            ))},
        )
        conn.execute(text("INSERT INTO langchain_pg_embedding VALUES ('chunk-a','collection-1',:metadata)"), {
            "metadata": json.dumps({"source_external_id": "source-a", "chunk_revision_external_id": "chunk-a-r3"})
        })
    with Session(engine) as session:
        yield session, org
    engine.dispose()


def _check(db, org, **overrides):
    options = {
        "organization_id": org,
        "source_external_id": "source-a",
        "index_external_id": "index-a",
        "collection_name": "policy-index-physical",
    }
    options.update(overrides)
    return inspect_pgvector_causal_target(db, **options)


def test_collection_preflight_is_read_only(prepared_database):
    db, org = prepared_database
    observed_sql = []
    event.listen(db.bind, "before_cursor_execute", lambda c, cursor, statement, parameters, context, executemany: observed_sql.append(statement))
    result = _check(db, org)
    assert result.status == "ready"
    assert result.code == "collection_ready"
    assert observed_sql and all(s.lstrip().upper().startswith("SELECT") for s in observed_sql)


def test_chunk_preflight_checks_exact_revision(prepared_database):
    db, org = prepared_database
    result = _check(db, org, chunk_id="chunk-a", chunk_revision_external_id="chunk-a-r3")
    assert result.ready and result.code == "chunk_ready"
    assert result.to_dict() == {"status": "ready", "code": "chunk_ready", "detail": result.detail}


@pytest.mark.parametrize("overrides,code", [
    ({"collection_name": "missing"}, "collection_not_found"),
    ({"organization_id": uuid.UUID(int=1)}, "collection_identity_mismatch"),
    ({"source_external_id": "source-b"}, "collection_identity_mismatch"),
    ({"index_external_id": "index-b"}, "collection_identity_mismatch"),
    ({"chunk_id": "missing", "chunk_revision_external_id": "chunk-a-r3"}, "chunk_not_found"),
    ({"chunk_id": "chunk-a", "chunk_revision_external_id": "chunk-a-r4"}, "chunk_provenance_mismatch"),
    ({"collection_name": "' OR 1=1 --"}, "collection_not_found"),
])
def test_preflight_not_ready_with_stable_code(prepared_database, overrides, code):
    db, org = prepared_database
    result = _check(db, org, **overrides)
    assert not result.ready
    assert result.code == code


@pytest.mark.parametrize("metadata", ["{}", "null", "bad-json", '{"sde_organization_id":true}'])
def test_collection_missing_identity_fails_closed(prepared_database, metadata):
    db, org = prepared_database
    db.execute(text("UPDATE langchain_pg_collection SET cmetadata = :metadata"), {"metadata": metadata})
    assert _check(db, org).code == "collection_identity_missing"


@pytest.mark.parametrize("metadata", ["{}", "bad-json", '{"source_external_id":"source-a"}'])
def test_chunk_missing_provenance_fails_closed(prepared_database, metadata):
    db, org = prepared_database
    db.execute(text("UPDATE langchain_pg_embedding SET cmetadata = :metadata"), {"metadata": metadata})
    assert _check(db, org, chunk_id="chunk-a", chunk_revision_external_id="chunk-a-r3").code == "chunk_provenance_mismatch"


@pytest.mark.parametrize("kwargs", [
    {"chunk_id": "chunk-a"},
    {"chunk_revision_external_id": "chunk-a-r3"},
    {"chunk_id": " ", "chunk_revision_external_id": "chunk-a-r3"},
    {"index_external_id": " "},
])
def test_preflight_rejects_incomplete_input(prepared_database, kwargs):
    db, org = prepared_database
    with pytest.raises(ValueError):
        _check(db, org, **kwargs)


def test_cli_reports_missing_registry_without_leaking_dsn(monkeypatch, capsys):
    from scripts import lineage_pgvector_preflight as cli
    from src.services.lineage_downstream_connection_service import LineageDownstreamConnectionNotFoundError

    def fail(**_):
        raise LineageDownstreamConnectionNotFoundError("password=SECRET_INTERNAL")

    monkeypatch.setattr(cli, "resolve_lineage_downstream_connection", fail)
    result = cli.main([
        "--organization-id", str(uuid.uuid4()), "--source", "source-a", "--index", "index-a",
        "--connection-ref", "missing", "--collection", "some-collection",
    ])
    assert result == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "downstream_unavailable"
    assert "SECRET_INTERNAL" not in json.dumps(payload)


def test_cli_requires_chunk_id_revision_pair(capsys):
    from scripts import lineage_pgvector_preflight as cli

    code = cli.main([
        "--organization-id", str(uuid.uuid4()), "--source", "source-a", "--index", "index-a",
        "--connection-ref", "missing", "--collection", "some-collection", "--chunk-id", "chunk-a",
    ])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["code"] == "invalid_arguments"


def test_cli_healthy_path_with_real_registry_resolution(tmp_path, monkeypatch, capsys):
    from scripts import lineage_pgvector_preflight as cli
    from src.services.lineage_downstream_connection_service import settings

    database_url = f"sqlite:///{tmp_path / 'preflight.db'}"
    org = uuid.uuid4()
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE langchain_pg_collection (uuid TEXT PRIMARY KEY, name TEXT, cmetadata TEXT)"))
        conn.execute(text("CREATE TABLE langchain_pg_embedding (id TEXT PRIMARY KEY, collection_id TEXT, cmetadata TEXT)"))
        conn.execute(text("INSERT INTO langchain_pg_collection VALUES ('c1', 'real-collection', :metadata)"), {
            "metadata": json.dumps(sde_pgvector_collection_metadata(
                organization_id=org, source_external_id="source-a", index_external_id="index-a"
            ))
        })
        conn.execute(text("INSERT INTO langchain_pg_embedding VALUES ('chunk-a','c1',:metadata)"), {
            "metadata": json.dumps({"source_external_id": "source-a", "chunk_revision_external_id": "chunk-a-r3"})
        })
    engine.dispose()
    monkeypatch.setattr(settings, "DOWNSTREAM_DATABASE_URLS", json.dumps({"local-test-db": database_url}))

    code = cli.main([
        "--organization-id", str(org), "--source", "source-a", "--index", "index-a",
        "--connection-ref", "local-test-db", "--collection", "real-collection",
        "--chunk-id", "chunk-a", "--chunk-revision", "chunk-a-r3",
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["code"] == "chunk_ready"
