"""Recovery 01.A2: two real disposable PostgreSQL stores and no serving grant.

No production/pilot connections or external providers. The downstream writes are
fixture materialization performed before the independent physical read.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.database import SessionLocal
from src.domain.lineage_execution_target import (
    LineageExecutionTargetBinding, LineageExecutionTargetIdentity,
)
from src.domain.lineage_observation import LineageObservation, LineageRevisionReference
from src.models.entity import Entity
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.user import User
from src.services.lineage_execution_target_binding_service import persist_lineage_execution_target_binding
from src.services.lineage_observation_persistence_service import persist_lineage_observation
from src.services.lineage_pilot_snapshot_reference_service import (
    PilotSnapshotReferenceError, _canonical_text, load_pilot_snapshot_reference,
)
from src.services.lineage_successor_recovery_preflight_service import (
    SuccessorPreflightBlocked, assess_verified_successor_recovery_candidate,
)
from src.services.lineage_successor_recovery_certificate_service import (
    SuccessorCertificateDenied, issue_successor_recovery_evidence_certificate,
)

AUTHORITY_URL = "postgresql://sde_integration:sde_local_test_only@db:5432/sde_integration"
DOWNSTREAM_URL = "postgresql://downstream_test:downstream_test@integration_downstream_db:5432/downstream_test"


def _enforce_disposable():
    if (os.getenv("ENVIRONMENT") != "test"
            or os.getenv("RUN_DB_INTEGRATION_TESTS") != "1"
            or os.getenv("DATABASE_URL") != AUTHORITY_URL
            or os.getenv("TEST_DATABASE_URL") != AUTHORITY_URL):
        raise RuntimeError("blocked:not_isolated_test_environment")
    from urllib.parse import urlsplit
    for url, host, dbname, user in (
        (AUTHORITY_URL, "db", "/sde_integration", "sde_integration"),
        (DOWNSTREAM_URL, "integration_downstream_db", "/downstream_test", "downstream_test"),
    ):
        parsed = urlsplit(url)
        if (parsed.hostname, parsed.path, parsed.username, parsed.port) != (host, dbname, user, 5432):
            raise RuntimeError("blocked:unexpected_database_target")


def _args(ids, root):
    return dict(
        authority_session_factory=SessionLocal,
        organization_id=ids["org"], source_id=ids["source"],
        expected_source_url=ids["url"], expected_snapshot_id=ids["snapshot"],
        expected_snapshot_sha256=ids["sha"], snapshot_root=root,
    )


def _seed_authority(root):
    idmap = {k: uuid.uuid4() for k in ("org", "other", "admin", "viewer", "other_admin", "source", "snapshot")}
    idmap["url"] = "https://recovery01-a2.github.io/safe-pilot/"
    raw = b"<html><main><h1>Refunds</h1><p>Refunds accepted within seven days.</p></main></html>"
    (root / "snapshot.html").write_bytes(raw)
    idmap["sha"] = hashlib.sha256(raw).hexdigest()
    idmap["text"] = _canonical_text(raw)
    with SessionLocal() as db:
        for key in ("org", "other"):
            org = Organization(
                id=idmap[key], name=f"Recovery-A2-{key}",
                slug=f"recovery-a2-{key}-{uuid.uuid4().hex[:12]}",
                billing_email=f"{uuid.uuid4().hex[:9]}@example.invalid",
            )
            db.add(org)
        db.flush()
        for key, orgkey, role in (
            ("admin", "org", "admin"), ("viewer", "org", "viewer"),
            ("other_admin", "other", "admin"),
        ):
            db.add(User(
                id=idmap[key], organization_id=idmap[orgkey],
                email=f"{uuid.uuid4().hex}@example.invalid", full_name="A2 isolated", role=role,
                is_active=True,
            ))
        entity = Entity(
            id=uuid.uuid4(), organization_id=idmap["org"], name="A2 source",
            domain="recovery01-a2.github.io", category="test",
        )
        db.add(entity)
        db.flush()
        db.add(MonitoredPage(
            id=idmap["source"], organization_id=idmap["org"], entity_id=entity.id,
            url=idmap["url"], page_type="docs", crawl_frequency="daily", is_active=True,
        ))
        db.flush()
        db.add(PageSnapshot(
            id=idmap["snapshot"], organization_id=idmap["org"],
            monitored_page_id=idmap["source"], content_hash=idmap["sha"],
            raw_content_path=str(root / "snapshot.html"), raw_content=raw.decode(),
            content_size=len(raw),
        ))
        db.commit()
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        ref = load_pilot_snapshot_reference(
            db, organization_id=idmap["org"], source_id=idmap["source"],
            expected_source_url=idmap["url"], expected_snapshot_id=idmap["snapshot"],
            expected_snapshot_sha256=idmap["sha"], snapshot_root=root,
        )
        db.rollback()
    idmap["ref"] = ref
    with SessionLocal() as db:
        persist_lineage_observation(
            db, organization_id=idmap["org"], observation=LineageObservation(
                observation_kind="materialization", source_external_id=ref.source_external_id,
                snapshot_external_id=str(ref.snapshot_id), asset_external_id=ref.chunk_external_id,
                asset_kind="chunk", revision_external_id=ref.chunk_revision_external_id,
                sequence=1, dependencies=(),
            ),
        )
        persist_lineage_observation(
            db, organization_id=idmap["org"], observation=LineageObservation(
                observation_kind="materialization", source_external_id=ref.source_external_id,
                snapshot_external_id=str(ref.snapshot_id), asset_external_id=ref.index_external_id,
                asset_kind="index", revision_external_id=f"{ref.index_external_id}@{ref.snapshot_id}",
                sequence=1, dependencies=(LineageRevisionReference(
                    asset_external_id=ref.chunk_external_id,
                    revision_external_id=ref.chunk_revision_external_id,
                    dependency_type="indexed_from",
                ),),
            ),
        )
        persist_lineage_execution_target_binding(
            db, organization_id=idmap["org"], binding=LineageExecutionTargetBinding(
                identity=LineageExecutionTargetIdentity(
                    ref.source_external_id, ref.index_external_id,
                ), binding_revision=1, provider="pgvector",
                target_kind="langchain_pgvector_causal_chunk",
                configuration={"connection_ref": "recovery01-disposable", "collection_name": ref.collection_name},
            ),
        )
        db.commit()
    return idmap


def _seed_downstream(ids):
    ref = ids["ref"]
    engine = create_engine(DOWNSTREAM_URL)
    try:
        with engine.begin() as db:
            db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS langchain_pg_collection (
                    uuid UUID PRIMARY KEY, name VARCHAR NOT NULL UNIQUE, cmetadata JSON
                )
            """))
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS langchain_pg_embedding (
                    id VARCHAR PRIMARY KEY, collection_id UUID NOT NULL
                    REFERENCES langchain_pg_collection(uuid) ON DELETE CASCADE,
                    embedding VECTOR, document VARCHAR, cmetadata JSONB
                )
            """))
            ids["collection"] = uuid.uuid4()
            collection = dict(
                sde_organization_id=str(ids["org"]),
                sde_source_external_id=ref.source_external_id,
                sde_index_external_id=ref.index_external_id,
            )
            metadata = dict(
                source_external_id=ref.source_external_id,
                chunk_revision_external_id=ref.chunk_revision_external_id,
                snapshot_external_id=str(ids["snapshot"]),
            )
            db.execute(text("""
                INSERT INTO langchain_pg_collection(uuid, name, cmetadata)
                VALUES (:id, :name, CAST(:metadata AS json))
            """), {"id": ids["collection"], "name": ref.collection_name,
                   "metadata": json.dumps(collection)})
            db.execute(text("""
                INSERT INTO langchain_pg_embedding(id, collection_id, embedding, document, cmetadata)
                VALUES (:id, :collection, '[0,0,0,0,0,0,0,0]'::vector, :content, CAST(:metadata AS jsonb))
            """), {"id": ref.chunk_external_id, "collection": ids["collection"],
                   "content": ids["text"], "metadata": json.dumps(metadata)})
    finally:
        engine.dispose()


def _certificate_count():
    with SessionLocal() as db:
        n = db.execute(text("SELECT COUNT(*) FROM lineage_successor_recovery_certificates")).scalar_one()
        db.rollback()
    return n


def test_a2_two_real_databases_and_certificate_contract(monkeypatch, tmp_path):
    _enforce_disposable()
    # Required by source-backed connection resolution, NEVER a caller URL.
    from src.services.lineage_downstream_connection_service import settings
    monkeypatch.setattr(settings, "DOWNSTREAM_DATABASE_URLS", json.dumps({
        "recovery01-disposable": DOWNSTREAM_URL,
    }))
    with SessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM schema_migrations WHERE version = '049_lineage_successor_recovery_certificates.sql'")).scalar_one() == 1
        db.rollback()
    ids = _seed_authority(tmp_path)
    _seed_downstream(ids)
    kwargs = _args(ids, tmp_path)
    try:
        evidence = assess_verified_successor_recovery_candidate(**kwargs)
        assert evidence.witness_status == "digest_match" and evidence.grants_admission is False
        assert _certificate_count() == 0
        # A non-admin cannot ask to issue an independent certificate.
        with pytest.raises(SuccessorCertificateDenied, match="tenant_admin_required"):
            issue_successor_recovery_evidence_certificate(**kwargs, issuer_user_id=ids["viewer"])
        with pytest.raises(SuccessorCertificateDenied, match="tenant_admin_required"):
            issue_successor_recovery_evidence_certificate(**kwargs, issuer_user_id=ids["other_admin"])
        assert _certificate_count() == 0
        # Two independently verified concurrent attempts: same immutable row.
        def issue():
            return issue_successor_recovery_evidence_certificate(
                **kwargs, issuer_user_id=ids["admin"],
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(issue) for _ in range(2)]
            results = [future.result(timeout=30) for future in tasks]
        assert len({r.certificate_id for r in results}) == 1
        assert sorted(r.created for r in results) == [False, True]
        assert all(r.grants_admission is False for r in results)
        assert _certificate_count() == 1
        assert issue().created is False
        with SessionLocal() as db:
            row = db.execute(text("""
                SELECT id, snapshot_id, binding_revision, grants_admission,
                       chunk_content_sha256, issued_by_user_id
                FROM lineage_successor_recovery_certificates
            """)).one()
            assert row.snapshot_id == ids["snapshot"]
            assert row.binding_revision == 1
            assert row.grants_admission is False
            assert row.chunk_content_sha256 == ids["ref"].chunk_content_sha256
            assert row.issued_by_user_id == ids["admin"]
            cert_id = row.id
            db.rollback()
        # SQL prevents any covert admission grant (not merely Python logic).
        with pytest.raises(IntegrityError):
            with SessionLocal.begin() as db:
                db.execute(text("""
                    UPDATE lineage_successor_recovery_certificates
                    SET grants_admission = TRUE WHERE id=:cert
                """), {"cert": cert_id})
        # Composite FK blocks a foreign tenant reusing another tenant's observations.
        with pytest.raises(IntegrityError):
            with SessionLocal.begin() as db:
                db.execute(text("""
                    INSERT INTO lineage_successor_recovery_certificates (
                        id, organization_id, source_id, snapshot_id, snapshot_sha256,
                        target_source_external_id, target_asset_external_id,
                        target_revision_external_id, chunk_revision_external_id,
                        chunk_content_sha256, chunk_observation_id, index_observation_id,
                        binding_revision, issued_by_user_id
                    )
                    SELECT :new_id, :other_org, source_id, snapshot_id, snapshot_sha256,
                           target_source_external_id, target_asset_external_id,
                           target_revision_external_id || '-cross-tenant', chunk_revision_external_id,
                           chunk_content_sha256, chunk_observation_id, index_observation_id,
                           binding_revision, :other_admin
                    FROM lineage_successor_recovery_certificates WHERE id=:cert
                """), {"new_id": uuid.uuid4(), "other_org": ids["other"],
                       "other_admin": ids["other_admin"], "cert": cert_id})
        assert _certificate_count() == 1
        # Physical bytes changed AFTER certificate: replay must check again.
        downstream = create_engine(DOWNSTREAM_URL)
        try:
            with downstream.begin() as db:
                db.execute(text("""
                    UPDATE langchain_pg_embedding SET document='tampered physical content'
                    WHERE id=:chunk AND collection_id=:collection
                """), {"chunk": ids["ref"].chunk_external_id, "collection": ids["collection"]})
        finally:
            downstream.dispose()
        with pytest.raises(SuccessorPreflightBlocked, match="physical_digest_mismatch"):
            issue()
        assert _certificate_count() == 1
        # Even after physical repair, an advancing authority snapshot blocks replay.
        downstream = create_engine(DOWNSTREAM_URL)
        try:
            with downstream.begin() as db:
                db.execute(text("""
                    UPDATE langchain_pg_embedding SET document=:content
                    WHERE id=:chunk AND collection_id=:collection
                """), {"content": ids["text"], "chunk": ids["ref"].chunk_external_id,
                       "collection": ids["collection"]})
        finally:
            downstream.dispose()
        newraw = b"<html><main>Successor was superseded by another revision.</main></html>"
        newpath = tmp_path / 'next.html'
        newpath.write_bytes(newraw)
        with SessionLocal.begin() as db:
            db.add(PageSnapshot(
                id=uuid.uuid4(), organization_id=ids["org"],
                monitored_page_id=ids["source"], content_hash=hashlib.sha256(newraw).hexdigest(),
                raw_content_path=str(newpath), raw_content=newraw.decode(), content_size=len(newraw),
                crawled_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            ))
        with pytest.raises(PilotSnapshotReferenceError, match="not the latest"):
            issue()
        assert _certificate_count() == 1
    finally:
        # We delete the entire disposable Compose DBs after the test. No source cleanup.
        pass
