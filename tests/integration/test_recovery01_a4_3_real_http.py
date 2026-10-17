"""Recovery A4.3: real authenticated HTTP across isolated Docker network.

This test talks to uvicorn in another container. It uses the real PostgreSQL
API-key verifier, issuer and physical PGVector checker, no test auth overrides.
All stores have fixture-only credentials, no host ports or external providers.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, text

from src.core.database import SessionLocal
from src.core.security import token_hash
from src.models.api_key import ApiKey
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.user import User
from src.services.lineage_successor_recovery_certificate_service import (
    issue_successor_recovery_evidence_certificate,
)
from src.services.lineage_target_integrity_service import assess_lineage_target_integrity
from src.domain.lineage_revision import LineageRevisionIdentity
from src.integrations.langchain.recovery_context_admission import (
    PilotRecoveryContextNotAdmittedError,
    admit_pilot_recovery_document,
)
from tests.integration.test_recovery01_a2_real_certificate import (
    _args, _enforce_disposable, _seed_authority, _seed_downstream,
    AUTHORITY_URL, DOWNSTREAM_URL,
)

HTTP_ROOT = "http://api:8000"
ROUTE = "/api/lineage/recovery-context-admission/query"
SHARED_ROOT = Path("/app/data/recovery_snapshots")


def _api_key(org, *, read=True, active=True, expires_at=None):
    raw = "sde-a43-isolated-" + secrets.token_urlsafe(32)
    key_id = uuid.uuid4()
    with SessionLocal.begin() as db:
        db.add(ApiKey(
            id=key_id, organization_id=org, name="A4.3 isolated integration",
            key_hash=token_hash(raw), permissions={"read": read, "write": False},
            is_active=active, expires_at=expires_at,
        ))
    return raw, key_id


def _request(client, raw_key, payload):
    headers = {"X-API-Key": raw_key, "Content-Type": "application/json"} if raw_key else {"Content-Type": "application/json"}
    response = client.post(ROUTE, json=payload, headers=headers)
    if response.status_code == 200:
        assert response.headers.get("cache-control") == "no-store, private", (response.status_code, response.text[:400])
    return response


def _expect_block(client, raw_key, payload, *, label):
    response = _request(client, raw_key, payload)
    assert response.status_code == 200, (label, response.status_code, response.text[:400])
    data = response.json()
    assert data["outcome"] == "block", (label, data)
    assert data["reason"] != "verified_successor", label
    assert data["request_scoped"] is True
    assert data["certificate_id"] is None and data["chunk_content_sha256"] is None
    return data["reason"]


def test_a4_3_real_http_authority_certificate_and_pgvector():
    _enforce_disposable()
    assert os.environ.get("SDE_LINEAGE_ACCEPTANCE_API_URL") == HTTP_ROOT
    assert SHARED_ROOT.is_dir(), "A4.3 shared snapshot volume absent"
    # Hard guard: no host / production database targets; no external provider calls.
    assert os.getenv("DATABASE_URL") == AUTHORITY_URL
    assert os.getenv("SDE_RECOVERY_CONTEXT_ADMISSION_ENABLED") == "true"
    assert os.getenv("SDE_RECOVERY_SNAPSHOT_ROOT") == str(SHARED_ROOT)
    mapping = json.loads(os.environ["SDE_DOWNSTREAM_DATABASE_URLS"])
    assert mapping == {"recovery01-disposable": DOWNSTREAM_URL}

    root = SHARED_ROOT / ("a43_" + uuid.uuid4().hex)
    root.mkdir(mode=0o700, parents=False)
    ids = _seed_authority(root)
    _seed_downstream(ids)
    ref = ids["ref"]
    with SessionLocal() as db:
        target = LineageRevisionIdentity(
            ref.source_external_id, ref.index_external_id,
            f"{ref.index_external_id}@{ref.snapshot_id}",
        )
        assert assess_lineage_target_integrity(
            db, organization_id=ids["org"], target=target,
        ).status == "unknown"
        db.rollback()

    # A4.3 exercises real token hashing and the existing HTTP auth dependency.
    valid_key, valid_id = _api_key(ids["org"])
    no_read, _ = _api_key(ids["org"], read=False)
    foreign_key, _ = _api_key(ids["other"])
    payload = {
        "source_id": str(ids["source"]), "snapshot_id": str(ids["snapshot"]),
        "chunk_external_id": ref.chunk_external_id,
        "chunk_revision_external_id": ref.chunk_revision_external_id,
        "index_revision_external_id": f"{ref.index_external_id}@{ref.snapshot_id}",
        "frozen_content_sha256": ref.chunk_content_sha256,
    }
    # Read the actual stored document, not a synthetic consumer fixture.
    physical_engine = create_engine(DOWNSTREAM_URL)
    try:
        with physical_engine.connect() as physical_db:
            row = physical_db.execute(
                text(
                    "SELECT id, document, cmetadata FROM langchain_pg_embedding "
                    "WHERE collection_id = :collection AND id = :chunk"
                ),
                {
                    "collection": ids["collection"],
                    "chunk": ref.chunk_external_id,
                },
            ).one()
            retrieved_document = SimpleNamespace(
                id=row[0], page_content=row[1], metadata=row[2],
            )
    finally:
        physical_engine.dispose()

    with httpx.Client(
        base_url=HTTP_ROOT, timeout=20.0, trust_env=False,
    ) as client, httpx.Client(
        base_url=HTTP_ROOT, timeout=20.0, trust_env=False,
        headers={"X-API-Key": valid_key},
    ) as consumer:
        # Cannot proceed solely due to PGVector and materialization match.
        assert _expect_block(client, valid_key, payload, label="missing_certificate") != "unsupported"
        with pytest.raises(PilotRecoveryContextNotAdmittedError) as pending:
            admit_pilot_recovery_document(
                http_client=consumer,
                source_id=ids["source"],
                snapshot_id=ids["snapshot"],
                retrieved_document=retrieved_document,
            )
        assert pending.value.reason == "evidence_unavailable"
        assert _request(client, None, payload).status_code == 401
        assert _request(client, "invalid-test-key", payload).status_code == 401
        assert _request(client, no_read, payload).status_code == 403
        _expect_block(client, foreign_key, payload, label="foreign_tenant")
        unauth = _request(client, valid_key, {**payload, "organization_id": str(ids["other"])})
        assert unauth.status_code == 422
        # A2 real issuer re-verifies physical before certificate persistence.
        cert = issue_successor_recovery_evidence_certificate(
            **_args(ids, root), issuer_user_id=ids["admin"],
        )
        assert cert.grants_admission is False
        accepted = _request(client, valid_key, payload)
        assert accepted.status_code == 200, accepted.text
        body = accepted.json()
        assert body == {
            "outcome": "proceed", "reason": "verified_successor",
            "snapshot_id": str(ids["snapshot"]),
            "certificate_id": str(cert.certificate_id),
            "chunk_content_sha256": ref.chunk_content_sha256,
            "request_scoped": True,
        }, body

        # The consumer must pass the real PGVector document to SDE.
        admitted = admit_pilot_recovery_document(
            http_client=consumer,
            source_id=ids["source"],
            snapshot_id=ids["snapshot"],
            retrieved_document=retrieved_document,
        )
        assert admitted.content == ids["text"]
        assert admitted.content_sha256 == ref.chunk_content_sha256
        assert admitted.certificate_id == cert.certificate_id

        # Only the frozen copy is eligible for downstream generation.
        retrieved_document.page_content = "changed after admission"
        assert admitted.content == ids["text"]
        retrieved_document.page_content = ids["text"]

        with SessionLocal() as db:
            assert db.query(ApiKey).filter(ApiKey.id == valid_id).one().last_used_at is not None
            assert db.execute(text(
                "SELECT grants_admission FROM lineage_successor_recovery_certificates WHERE id=:id"
            ), {"id": cert.certificate_id}).scalar_one() is False
            db.rollback()
        # Caller freezes/uses bytes: false consumer digest cannot bypass witness.
        _expect_block(client, valid_key, {**payload, "frozen_content_sha256": "0"*64}, label="consumer_digest_tamper")
        _expect_block(client, valid_key, {**payload, "chunk_revision_external_id": "wrong-revision"}, label="revision_tamper")
        # Revoking the issuer must immediately remove recovery admission.
        with SessionLocal.begin() as db:
            assert db.execute(text("UPDATE users SET role='viewer' WHERE id=:id"), {"id": ids["admin"]}).rowcount == 1
        _expect_block(client, valid_key, payload, label="issuer_revoked")
        with SessionLocal.begin() as db:
            db.execute(text("UPDATE users SET role='admin' WHERE id=:id"), {"id": ids["admin"]})
        assert _request(client, valid_key, payload).json()["outcome"] == "proceed"
        # Updating a physical chunk after issuance cannot reuse the certificate.
        engine = create_engine(DOWNSTREAM_URL)
        try:
            with engine.begin() as db:
                assert db.execute(text("""
                    UPDATE langchain_pg_embedding SET document=:document
                    WHERE id=:id AND collection_id=:collection
                """), {"document": "untrusted mutable payload", "id": ref.chunk_external_id,
                        "collection": ids["collection"]}).rowcount == 1
            _expect_block(client, valid_key, payload, label="pgvector_mutated")
            with pytest.raises(PilotRecoveryContextNotAdmittedError) as mutated:
                admit_pilot_recovery_document(
                    http_client=consumer,
                    source_id=ids["source"],
                    snapshot_id=ids["snapshot"],
                    retrieved_document=retrieved_document,
                )
            assert mutated.value.reason == "verification_failed"
            with engine.begin() as db:
                db.execute(text("""
                    UPDATE langchain_pg_embedding SET document=:document
                    WHERE id=:id AND collection_id=:collection
                """), {"document": ids["text"], "id": ref.chunk_external_id,
                        "collection": ids["collection"]})
        finally:
            engine.dispose()
        assert _request(client, valid_key, payload).json()["outcome"] == "proceed"
        # API key read revocation is checked both by the HTTP dependency and service.
        with SessionLocal.begin() as db:
            db.execute(text("UPDATE api_keys SET is_active=FALSE WHERE id=:id"), {"id": valid_id})
        assert _request(client, valid_key, payload).status_code == 401
        # Superseding upstream snapshot must block regardless of the old certificate.
        with SessionLocal.begin() as db:
            db.execute(text("UPDATE api_keys SET is_active=TRUE WHERE id=:id"), {"id": valid_id})
        newer = b"<html><main>Refund policy superseded once more.</main></html>"
        next_path = root / "newest.html"
        next_path.write_bytes(newer)
        with SessionLocal.begin() as db:
            db.add(PageSnapshot(
                id=uuid.uuid4(), organization_id=ids["org"], monitored_page_id=ids["source"],
                content_hash=hashlib.sha256(newer).hexdigest(),
                raw_content_path=str(next_path), raw_content=newer.decode(), content_size=len(newer),
                crawled_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            ))
        _expect_block(client, valid_key, payload, label="superseded_snapshot")
        with pytest.raises(PilotRecoveryContextNotAdmittedError) as superseded:
            admit_pilot_recovery_document(
                http_client=consumer,
                source_id=ids["source"],
                snapshot_id=ids["snapshot"],
                retrieved_document=retrieved_document,
            )
        assert superseded.value.reason == "evidence_unavailable"

    print("PASS: A4.3 real HTTP + physical PGVector + pilot consumer recovery admission", flush=True)
