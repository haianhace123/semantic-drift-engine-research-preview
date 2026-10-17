"""Independent, read-only eligibility proof for one SDE pilot PGVector successor.

NO admission grant is issued here.  This is a preflight for a future tenant-
authorized, transactional certificate issuer.  Do not use its return value to
allow model calls; `unknown` must still block in HTTP integrity admission.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from src.domain.lineage_execution_target import LineageExecutionTargetIdentity
from src.domain.lineage_revision import LineageRevisionIdentity
from src.models.lineage_observation import LineageObservationRecord
from src.services.lineage_downstream_connection_service import (
    create_lineage_downstream_session,
    resolve_lineage_downstream_connection,
)
from src.services.lineage_execution_target_binding_service import (
    load_current_lineage_execution_target_binding,
)
from src.services.lineage_pgvector_invalidation_service import (
    LANGCHAIN_PGVECTOR_CAUSAL_CHUNK_TARGET_KIND,
    PGVECTOR_PROVIDER,
)
from src.services.lineage_pgvector_physical_witness_service import (
    inspect_pgvector_chunk_physical_witness,
)
from src.services.lineage_pilot_snapshot_reference_service import (
    PilotSnapshotReference,
    load_pilot_snapshot_reference,
)
from src.services.lineage_target_integrity_service import (
    assess_lineage_target_integrity,
)


class SuccessorPreflightBlocked(RuntimeError):
    """Evidence insufficient for *considering* a new recovery certificate."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Successor preflight blocked: {code}")


@dataclass(frozen=True, slots=True)
class _AuthorityFacts:
    reference: PilotSnapshotReference
    target: LineageRevisionIdentity
    chunk_observation_id: uuid.UUID
    index_observation_id: uuid.UUID
    binding_revision: int
    connection_ref: str


@dataclass(frozen=True, slots=True)
class SuccessorRecoveryPreflight:
    """Non-authorizing proof only; cannot be used as a serving credential."""

    organization_id: uuid.UUID
    source_id: uuid.UUID
    snapshot_id: uuid.UUID
    target: LineageRevisionIdentity
    chunk_revision_external_id: str
    chunk_sha256: str
    chunk_observation_id: uuid.UUID
    index_observation_id: uuid.UUID
    binding_revision: int
    witness_status: str
    grants_admission: bool = False


def _read_only(factory: Callable[[], Session], action):
    session = factory()
    try:
        if session.in_transaction():
            raise SuccessorPreflightBlocked("existing_transaction")
        if session.get_bind().dialect.name != "postgresql":
            raise SuccessorPreflightBlocked("non_postgres_authority")
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        return action(session)
    finally:
        try:
            session.rollback()
        finally:
            session.close()


def _latest_observation(
    db: Session,
    *,
    organization_id: uuid.UUID,
    reference: PilotSnapshotReference,
    asset_external_id: str,
) -> LineageObservationRecord:
    row = (
        db.query(LineageObservationRecord)
        .options(joinedload(LineageObservationRecord.dependencies))
        .filter(
            LineageObservationRecord.organization_id == organization_id,
            LineageObservationRecord.source_external_id == reference.source_external_id,
            LineageObservationRecord.asset_external_id == asset_external_id,
            LineageObservationRecord.observation_kind == "materialization",
        )
        .order_by(LineageObservationRecord.sequence.desc(), LineageObservationRecord.id.desc())
        .first()
    )
    if row is None:
        raise SuccessorPreflightBlocked("materialization_missing")
    return row


def _authority_facts(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_id: uuid.UUID,
    source_url: str,
    snapshot_id: uuid.UUID,
    snapshot_sha256: str,
    snapshot_root: Path,
) -> _AuthorityFacts:
    reference = load_pilot_snapshot_reference(
        db,
        organization_id=organization_id,
        source_id=source_id,
        expected_source_url=source_url,
        expected_snapshot_id=snapshot_id,
        expected_snapshot_sha256=snapshot_sha256,
        snapshot_root=snapshot_root,
    )
    if (
        reference.organization_id != organization_id
        or reference.source_id != source_id
        or reference.snapshot_id != snapshot_id
        or reference.snapshot_sha256 != snapshot_sha256
    ):
        raise SuccessorPreflightBlocked("authority_reference_mismatch")
    target = LineageRevisionIdentity(
        reference.source_external_id,
        reference.index_external_id,
        f"{reference.index_external_id}@{reference.snapshot_id}",
    )
    # A no_action fixture is NOT a certificate.  Only an as-yet-undecided
    # successor is accepted for an independent recovery proof.
    if assess_lineage_target_integrity(
        db, organization_id=organization_id, target=target,
    ).status != "unknown":
        raise SuccessorPreflightBlocked("successor_not_unknown")

    chunk = _latest_observation(
        db, organization_id=organization_id, reference=reference,
        asset_external_id=reference.chunk_external_id,
    )
    index = _latest_observation(
        db, organization_id=organization_id, reference=reference,
        asset_external_id=reference.index_external_id,
    )
    if (
        chunk.asset_kind != "chunk"
        or chunk.snapshot_external_id != str(reference.snapshot_id)
        or chunk.revision_external_id != reference.chunk_revision_external_id
        or chunk.dependencies
    ):
        raise SuccessorPreflightBlocked("chunk_materialization_mismatch")
    if (
        index.asset_kind != "index"
        or index.snapshot_external_id != str(reference.snapshot_id)
        or index.revision_external_id != target.revision_external_id
        or len(index.dependencies) != 1
    ):
        raise SuccessorPreflightBlocked("index_materialization_mismatch")
    dep = index.dependencies[0]
    if (
        dep.asset_external_id != reference.chunk_external_id
        or dep.revision_external_id != reference.chunk_revision_external_id
        or dep.dependency_type != "indexed_from"
    ):
        raise SuccessorPreflightBlocked("index_dependency_mismatch")

    binding = load_current_lineage_execution_target_binding(
        db,
        organization_id=organization_id,
        identity=LineageExecutionTargetIdentity(
            source_external_id=reference.source_external_id,
            asset_external_id=reference.index_external_id,
        ),
    )
    if binding is None:
        raise SuccessorPreflightBlocked("binding_missing")
    if (
        binding.provider != PGVECTOR_PROVIDER
        or binding.target_kind != LANGCHAIN_PGVECTOR_CAUSAL_CHUNK_TARGET_KIND
        or set(binding.configuration) != {"connection_ref", "collection_name"}
        or binding.configuration.get("collection_name") != reference.collection_name
    ):
        raise SuccessorPreflightBlocked("binding_mismatch")
    connection_ref = binding.configuration.get("connection_ref")
    if not isinstance(connection_ref, str) or not connection_ref.strip():
        raise SuccessorPreflightBlocked("connection_ref_missing")
    return _AuthorityFacts(
        reference=reference,
        target=target,
        chunk_observation_id=chunk.id,
        index_observation_id=index.id,
        binding_revision=int(binding.binding_revision),
        connection_ref=connection_ref,
    )


def assess_verified_successor_recovery_candidate(
    *,
    authority_session_factory: Callable[[], Session],
    organization_id: uuid.UUID,
    source_id: uuid.UUID,
    expected_source_url: str,
    expected_snapshot_id: uuid.UUID,
    expected_snapshot_sha256: str,
    snapshot_root: Path,
) -> SuccessorRecoveryPreflight:
    """Verify exact present-tense successor evidence without granting access.

    Registry resolution uses the *tenant-scoped current binding*, never an
    untrusted connection URL in the request.  Fresh authority re-read after
    PGVector prevents trusting a stale initial PostgreSQL snapshot.  This
    does not provide cross-database atomicity, a lease, or a serving grant.
    """
    if not isinstance(organization_id, uuid.UUID) or not isinstance(source_id, uuid.UUID):
        raise TypeError("organization_id and source_id must be UUIDs")
    if not isinstance(expected_snapshot_id, uuid.UUID):
        raise TypeError("expected_snapshot_id must be UUID")
    if not isinstance(snapshot_root, Path):
        raise TypeError("snapshot_root must be Path")
    args = dict(
        organization_id=organization_id, source_id=source_id,
        source_url=expected_source_url, snapshot_id=expected_snapshot_id,
        snapshot_sha256=expected_snapshot_sha256, snapshot_root=snapshot_root,
    )
    original = _read_only(
        authority_session_factory, lambda db: _authority_facts(db, **args),
    )
    # Connection URL is resolved from the server's trusted registry, not a
    # request payload or PGVector collection metadata.
    connection = resolve_lineage_downstream_connection(
        connection_ref=original.connection_ref,
    )
    session, engine = create_lineage_downstream_session(connection)
    try:
        witness = _read_only(
            lambda: session,
            lambda db: inspect_pgvector_chunk_physical_witness(
                db,
                organization_id=organization_id,
                source_external_id=original.reference.source_external_id,
                index_external_id=original.reference.index_external_id,
                collection_name=original.reference.collection_name,
                chunk_external_id=original.reference.chunk_external_id,
                chunk_revision_external_id=original.reference.chunk_revision_external_id,
                snapshot_external_id=str(original.reference.snapshot_id),
                expected_content_sha256=original.reference.chunk_content_sha256,
            ),
        )
    finally:
        engine.dispose()
    if not witness.matches_reference_content:
        raise SuccessorPreflightBlocked(f"physical_{witness.status}")

    current = _read_only(
        authority_session_factory, lambda db: _authority_facts(db, **args),
    )
    if current != original:
        raise SuccessorPreflightBlocked("authority_changed_after_physical_read")
    return SuccessorRecoveryPreflight(
        organization_id=organization_id,
        source_id=source_id,
        snapshot_id=original.reference.snapshot_id,
        target=original.target,
        chunk_revision_external_id=original.reference.chunk_revision_external_id,
        chunk_sha256=original.reference.chunk_content_sha256,
        chunk_observation_id=original.chunk_observation_id,
        index_observation_id=original.index_observation_id,
        binding_revision=original.binding_revision,
        witness_status=witness.status,
    )
