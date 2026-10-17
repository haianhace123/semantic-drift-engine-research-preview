"""Real isolated SDE HTTP + authority PostgreSQL + PGVector serving boundary.

All writes in this test are fixture writes to the disposable Compose project's
sde_integration and downstream_test databases. The pilot orchestrator itself
reads authority and PGVector in distinct transactions and never writes either.
Successful admission below uses controlled *no_action* test decisions, NOT a
claim that SDE autonomously classified the source as safe.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from experiments.verified_pilot_serving import (
    VerifiedPilotServingBlocked,
    run_verified_pilot_model_call,
)
from src.core.database import SessionLocal
from src.domain.lineage_invalidation import (
    LineageInvalidationDecision,
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_revision import LineageRevisionIdentity
from src.integrations.langchain.context_admission import LangChainContextNotAdmittedError
from src.models.entity import Entity
from src.models.monitored_page import MonitoredPage
from src.models.page_snapshot import PageSnapshot
from src.services.lineage_invalidation_persistence_service import (
    persist_lineage_invalidation_decision,
)
from src.services.lineage_pilot_snapshot_reference_service import (
    PilotSnapshotReferenceError,
    load_pilot_snapshot_reference,
)
from tests.integration.test_lineage_invalidation_execution_lifecycle import (
    DOWNSTREAM_DATABASE_URL,
    _cleanup,
    _cleanup_downstream_collections,
    _ensure_downstream_schema,
    _insert_collection,
    _insert_document,
    _make_organization,
)
from tests.integration.test_lineage_physical_recovery_acceptance import _http_client
from tests.integration.test_temporal_event_http_admission_acceptance import (
    _cleanup_source_evidence,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="Disposable PostgreSQL/HTTP tests only.",
)


def _require_disposable_environment() -> None:
    """Second barrier, independent of the CLI runner's environment guard."""
    from scripts.ci_real_lineage_admission_gate import require_disposable_lineage_databases
    from urllib.parse import urlsplit

    require_disposable_lineage_databases()
    url = urlsplit(DOWNSTREAM_DATABASE_URL)
    if not (
        url.scheme in {"postgresql", "postgresql+psycopg2", "postgresql+psycopg"}
        and url.hostname == "integration_downstream_db"
        and url.port == 5432
        and url.username == "downstream_test"
        and url.password == "downstream_test"
        and url.path == "/downstream_test"
        and not url.query
        and not url.fragment
    ):
        raise RuntimeError("Refusing non-disposable physical database")


@dataclass
class RealPilot:
    authority_factory: object
    physical_factory: object
    root: Path
    org: uuid.UUID
    source_id: uuid.UUID
    url: str
    snapshot_id: uuid.UUID
    sha256: str
    text: str
    chunk_id: str
    collection_id: uuid.UUID
    client: object
    downstream_engine: object
    prompts: list[str]

    def read_document(self):
        with Session(self.downstream_engine) as session:
            row = session.execute(text(
                "SELECT id, document, cmetadata FROM langchain_pg_embedding "
                "WHERE collection_id=:collection AND id=:chunk"
            ), {"collection": self.collection_id, "chunk": self.chunk_id}).one()
            session.rollback()
        return SimpleNamespace(id=row[0], page_content=row[1], metadata=dict(row[2]))

    def kwargs(self, document=None, client=None):
        return dict(
            question="What is the current pilot rule?",
            retrieve=lambda _: [document if document is not None else self.read_document()],
            client=self.client if client is None else client,
            invoke_model=lambda prompt: self.prompts.append(prompt) or "approved-response",
            authority_session_factory=self.authority_factory,
            physical_session_factory=self.physical_factory,
            snapshot_root=self.root,
            organization_id=self.org,
            source_id=self.source_id,
            expected_source_url=self.url,
            expected_snapshot_id=self.snapshot_id,
            expected_snapshot_sha256=self.sha256,
        )

    def write_physical_content(self, content: str):
        with Session(self.downstream_engine) as session:
            result = session.execute(text(
                "UPDATE langchain_pg_embedding SET document=:content "
                "WHERE collection_id=:collection AND id=:chunk"
            ), {"content": content, "collection": self.collection_id, "chunk": self.chunk_id})
            assert result.rowcount == 1
            session.commit()

    def seed_no_action(self):
        source = f"sde-pilot-source:{self.source_id}"
        chunk = self.chunk_id
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        index = f"sde-pilot-index:{self.source_id}"
        root = LineageRevisionIdentity(source, "test-source-root", "test-root-v1")
        policy = LineageInvalidationPolicyIdentity(
            policy_external_id=f"verified-serving-acceptance-{self.source_id}", revision=1
        )
        with SessionLocal() as session:
            for target in (
                LineageRevisionIdentity(source, index, f"{index}@{self.snapshot_id}"),
                LineageRevisionIdentity(source, chunk, f"{chunk}:{digest[:16]}"),
            ):
                persist_lineage_invalidation_decision(
                    session,
                    organization_id=self.org,
                    decision=LineageInvalidationDecision(
                        root=root, target=target, policy=policy,
                        disposition="no_action",
                        reason_code="controlled_acceptance_no_action",
                        causal_path=(root, target),
                    ),
                )
            session.commit()


@pytest.fixture
def pilot(tmp_path: Path):
    _require_disposable_environment()
    db = SessionLocal()
    physical_engine = create_engine(DOWNSTREAM_DATABASE_URL, pool_pre_ping=True)
    physical = Session(physical_engine)
    org_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []
    client = None
    try:
        _ensure_downstream_schema(physical)
        owner = _make_organization(db)
        org_ids.append(owner.id)
        entity = Entity(
            id=uuid.uuid4(), organization_id=owner.id,
            name="Isolated pilot source", domain="sde-pilot-acceptance.github.io",
            category="test",
        )
        db.add(entity)
        db.flush()
        source_url = f"https://sde-pilot-acceptance.github.io/policy/{uuid.uuid4().hex}/"
        page = MonitoredPage(
            id=uuid.uuid4(), organization_id=owner.id,
            entity_id=entity.id, url=source_url, page_type="docs",
            crawl_frequency="daily", is_active=True,
        )
        db.add(page)
        db.flush()
        content = b"<html><nav>Ignore</nav><main><h1>Pilot</h1><p>Rule v2</p></main></html>"
        artifact = tmp_path / "pilot_snapshot.txt"
        artifact.write_bytes(content)
        raw_hash = hashlib.sha256(content).hexdigest()
        snapshot = PageSnapshot(
            id=uuid.uuid4(), organization_id=owner.id,
            monitored_page_id=page.id, content_hash=raw_hash,
            raw_content_path=str(artifact), raw_content=content.decode("utf-8"),
            content_size=len(content),
        )
        db.add(snapshot)
        db.commit()
        owner_id, page_id, snap_id = owner.id, page.id, snapshot.id
        client = _http_client(db, owner_id)
        with SessionLocal() as verify:
            verify.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            reference = load_pilot_snapshot_reference(
                verify, organization_id=owner_id, source_id=page_id,
                expected_source_url=source_url, expected_snapshot_id=snap_id,
                expected_snapshot_sha256=raw_hash, snapshot_root=tmp_path,
            )
            verify.rollback()
        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        _insert_collection(
            physical, collection_id=collection_id,
            collection_name=reference.collection_name, organization_id=owner_id,
        )
        _insert_document(
            physical, collection_id=collection_id,
            document_id=reference.chunk_external_id,
            document_revision_external_id=reference.chunk_revision_external_id,
        )
        collection_metadata = {
            "sde_organization_id": str(owner_id),
            "sde_source_external_id": reference.source_external_id,
            "sde_index_external_id": reference.index_external_id,
        }
        document_metadata = {
            "source_external_id": reference.source_external_id,
            "chunk_revision_external_id": reference.chunk_revision_external_id,
            "snapshot_external_id": str(snap_id),
        }
        physical.execute(text(
            "UPDATE langchain_pg_collection SET cmetadata=CAST(:meta AS jsonb) "
            "WHERE uuid=:collection"
        ), {"meta": json.dumps(collection_metadata), "collection": collection_id})
        result = physical.execute(text(
            "UPDATE langchain_pg_embedding "
            "SET document=:content, cmetadata=CAST(:meta AS jsonb) "
            "WHERE collection_id=:collection AND id=:chunk"
        ), {
            "content": "Pilot Rule v2", "meta": json.dumps(document_metadata),
            "collection": collection_id, "chunk": reference.chunk_external_id,
        })
        assert result.rowcount == 1
        physical.commit()
        instance = RealPilot(
            authority_factory=SessionLocal,
            physical_factory=lambda: Session(physical_engine),
            root=tmp_path, org=owner_id, source_id=page_id,
            url=source_url, snapshot_id=snap_id, sha256=raw_hash,
            text="Pilot Rule v2", chunk_id=reference.chunk_external_id,
            collection_id=collection_id, client=client,
            downstream_engine=physical_engine, prompts=[],
        )
        yield instance
    finally:
        if client is not None:
            client.close()
        physical.rollback()
        physical.close()
        db.rollback()
        db.close()
        physical_engine.dispose()
        _cleanup_downstream_collections(collection_ids)
        _cleanup_source_evidence(org_ids)
        _cleanup(org_ids)


def test_real_http_pgvector_exact_prompt_admits_once(pilot: RealPilot):
    pilot.seed_no_action()
    result = run_verified_pilot_model_call(**pilot.kwargs())
    assert result.output == "approved-response"
    assert result.admission_reason == "no_unresolved_impact"
    assert len(pilot.prompts) == 1
    assert pilot.text in pilot.prompts[0]
    assert result.chunk_content_sha256 == hashlib.sha256(pilot.text.encode()).hexdigest()


def test_real_http_unknown_blocks_model(pilot: RealPilot):
    with pytest.raises(LangChainContextNotAdmittedError) as caught:
        run_verified_pilot_model_call(**pilot.kwargs())
    assert caught.value.reason == "unknown_integrity"
    assert pilot.prompts == []


def test_real_pgvector_digest_tamper_blocks_cached_prompt(pilot: RealPilot):
    pilot.seed_no_action()
    known_good = pilot.read_document()
    pilot.write_physical_content("Pilot Rule CORRUPTED")
    with pytest.raises(VerifiedPilotServingBlocked) as caught:
        run_verified_pilot_model_call(**pilot.kwargs(document=known_good))
    assert caught.value.reason == "physical_digest_mismatch"
    assert pilot.prompts == []


def test_real_retrieved_prompt_tamper_blocks_even_when_physical_is_good(pilot: RealPilot):
    pilot.seed_no_action()
    doc = pilot.read_document()
    doc.page_content = "Pilot Rule manipulated in memory"
    with pytest.raises(VerifiedPilotServingBlocked) as caught:
        run_verified_pilot_model_call(**pilot.kwargs(document=doc))
    assert caught.value.reason == "prompt_content_digest_mismatch"
    assert pilot.prompts == []


def test_real_authority_advances_during_http_then_blocks_model(pilot: RealPilot):
    pilot.seed_no_action()

    class AdvancingClient:
        def query_context_integrity(self, targets):
            new_content = b"<html><main>New pilot rule</main></html>"
            new_artifact = pilot.root / "new_snapshot.txt"
            new_artifact.write_bytes(new_content)
            with SessionLocal() as db:
                db.add(PageSnapshot(
                    id=uuid.uuid4(), organization_id=pilot.org,
                    monitored_page_id=pilot.source_id,
                    content_hash=hashlib.sha256(new_content).hexdigest(),
                    raw_content_path=str(new_artifact),
                    raw_content=new_content.decode("utf-8"),
                    content_size=len(new_content),
                    crawled_at=datetime.now(timezone.utc) + timedelta(days=1),
                ))
                db.commit()
            return pilot.client.query_context_integrity(targets)

    with pytest.raises(PilotSnapshotReferenceError, match="not the latest"):
        run_verified_pilot_model_call(**pilot.kwargs(client=AdvancingClient()))
    assert pilot.prompts == []
