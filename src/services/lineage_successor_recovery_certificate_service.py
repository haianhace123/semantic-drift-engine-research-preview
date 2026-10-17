"""Tenant-admin-only issuance of evidence-only PGVector successor certificates.

This service has NO API endpoint and changes NO serving policy.  It must not
be called by untrusted automation or a request that has not authenticated its
user identity. The returned certificate is an audit artifact, not a bearer
credential or an HTTP admission decision.  A future reader MUST reverify the
physical target and current SDE authority at request time.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.models.user import User
from src.models.lineage_successor_recovery_certificate import LineageSuccessorRecoveryCertificateRecord
from src.services.lineage_authority_lock_service import acquire_lineage_target_authority_lock
from src.services.lineage_successor_recovery_preflight_service import (
    SuccessorRecoveryPreflight,
    _authority_facts,
    assess_verified_successor_recovery_candidate,
)


class SuccessorCertificateDenied(RuntimeError):
    """Issuer, authority, transaction, or conflicting evidence refused."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f'Certificate issuance denied: {code}')


@dataclass(frozen=True, slots=True)
class SuccessorCertificateIssueResult:
    certificate_id: uuid.UUID
    created: bool
    grants_admission: bool = False


def _enforce_transaction(db: Session) -> None:
    bind = db.get_bind()
    if bind.dialect.name != 'postgresql':
        raise SuccessorCertificateDenied('postgres_required')
    # The caller must start with a new transaction. A pre-existing repeatable-
    # read snapshot could make a stale successor look current.
    if db.in_transaction():
        raise SuccessorCertificateDenied('transaction_already_started')
    isolation = db.execute(text('SHOW transaction_isolation')).scalar_one()
    if isolation != 'read committed':
        raise SuccessorCertificateDenied('read_committed_required')


def _require_tenant_admin(db: Session, *, organization_id: uuid.UUID, issuer_user_id: uuid.UUID) -> None:
    issuer = (
        db.query(User)
        .filter(User.id == issuer_user_id, User.organization_id == organization_id,
                User.is_active.is_(True), User.deleted_at.is_(None))
        .one_or_none()
    )
    if issuer is None or issuer.role != 'admin':
        raise SuccessorCertificateDenied('tenant_admin_required')


def _same_issued_evidence(record: LineageSuccessorRecoveryCertificateRecord,
                          proof: SuccessorRecoveryPreflight,
                          issuer_user_id: uuid.UUID, source_id: uuid.UUID,
                          snapshot_sha256: str) -> bool:
    return (
        record.source_id == source_id
        and record.snapshot_id == proof.snapshot_id
        and record.snapshot_sha256 == snapshot_sha256
        and record.target_source_external_id == proof.target.source_external_id
        and record.target_asset_external_id == proof.target.asset_external_id
        and record.target_revision_external_id == proof.target.revision_external_id
        and record.chunk_revision_external_id == proof.chunk_revision_external_id
        and record.chunk_content_sha256 == proof.chunk_sha256
        and record.chunk_observation_id == proof.chunk_observation_id
        and record.index_observation_id == proof.index_observation_id
        and int(record.binding_revision) == proof.binding_revision
        and record.issued_by_user_id == issuer_user_id
        and record.evidence_kind == 'physical_successor_preflight_v1'
        and record.grants_admission is False
    )


def _fence_against_current_authority(db: Session, *, proof: SuccessorRecoveryPreflight,
                                     organization_id: uuid.UUID, source_id: uuid.UUID,
                                     source_url: str, snapshot_sha256: str,
                                     snapshot_root: Path) -> None:
    facts = _authority_facts(
        db, organization_id=organization_id, source_id=source_id, source_url=source_url,
        snapshot_id=proof.snapshot_id, snapshot_sha256=snapshot_sha256,
        snapshot_root=snapshot_root,
    )
    if (
        proof.organization_id != organization_id
        or proof.source_id != source_id
        or proof.snapshot_id != facts.reference.snapshot_id
        or proof.target != facts.target
        or proof.chunk_revision_external_id != facts.reference.chunk_revision_external_id
        or proof.chunk_sha256 != facts.reference.chunk_content_sha256
        or proof.chunk_observation_id != facts.chunk_observation_id
        or proof.index_observation_id != facts.index_observation_id
        or proof.binding_revision != facts.binding_revision
        or proof.witness_status != 'digest_match'
        or proof.grants_admission is not False
    ):
        raise SuccessorCertificateDenied('preflight_authority_fence_failed')


def issue_successor_recovery_evidence_certificate(
    *,
    authority_session_factory,
    organization_id: uuid.UUID,
    source_id: uuid.UUID,
    expected_source_url: str,
    expected_snapshot_id: uuid.UUID,
    expected_snapshot_sha256: str,
    snapshot_root: Path,
    issuer_user_id: uuid.UUID,
) -> SuccessorCertificateIssueResult:
    """Verify physical bytes ourselves, then persist exact tenant-scoped evidence.

    Returns evidence ONLY. No automatic clearance or serving status change.
    We own a fresh trusted write transaction after a separate, read-only
    physical preflight and always commit or rollback before returning.
    """
    for value in (organization_id, source_id, expected_snapshot_id, issuer_user_id):
        if not isinstance(value, uuid.UUID):
            raise TypeError('organization_id, source_id, snapshot_id and issuer_user_id must be UUID')
    if not isinstance(snapshot_root, Path):
        raise TypeError('snapshot_root must be Path')

    # Important: the service invokes the real witness itself. A caller cannot
    # submit a fabricated SuccessorRecoveryPreflight or supplied digest match.
    proof = assess_verified_successor_recovery_candidate(
        authority_session_factory=authority_session_factory,
        organization_id=organization_id,
        source_id=source_id,
        expected_source_url=expected_source_url,
        expected_snapshot_id=expected_snapshot_id,
        expected_snapshot_sha256=expected_snapshot_sha256,
        snapshot_root=snapshot_root,
    )
    if proof.grants_admission is not False or proof.witness_status != 'digest_match':
        raise SuccessorCertificateDenied('physical_proof_insufficient')

    db = authority_session_factory()
    try:
        _enforce_transaction(db)
        # Use identical target advisory lock to binding changes and invalidation.
        acquire_lineage_target_authority_lock(
            db, organization_id=organization_id,
            source_external_id=proof.target.source_external_id,
            asset_external_id=proof.target.asset_external_id,
        )
        _require_tenant_admin(db, organization_id=organization_id, issuer_user_id=issuer_user_id)
        _fence_against_current_authority(
            db, proof=proof, organization_id=organization_id, source_id=source_id,
            source_url=expected_source_url, snapshot_sha256=expected_snapshot_sha256,
            snapshot_root=snapshot_root,
        )
        existing = (
            db.query(LineageSuccessorRecoveryCertificateRecord)
            .filter(
                LineageSuccessorRecoveryCertificateRecord.organization_id == organization_id,
                LineageSuccessorRecoveryCertificateRecord.target_source_external_id == proof.target.source_external_id,
                LineageSuccessorRecoveryCertificateRecord.target_asset_external_id == proof.target.asset_external_id,
                LineageSuccessorRecoveryCertificateRecord.target_revision_external_id == proof.target.revision_external_id,
            ).one_or_none()
        )
        if existing is not None:
            if not _same_issued_evidence(existing, proof, issuer_user_id, source_id,
                                         expected_snapshot_sha256):
                raise SuccessorCertificateDenied('immutable_evidence_conflict')
            result = SuccessorCertificateIssueResult(existing.id, created=False)
        else:
            record = LineageSuccessorRecoveryCertificateRecord(
                id=uuid.uuid4(), organization_id=organization_id,
                source_id=source_id, snapshot_id=proof.snapshot_id,
                snapshot_sha256=expected_snapshot_sha256,
                target_source_external_id=proof.target.source_external_id,
                target_asset_external_id=proof.target.asset_external_id,
                target_revision_external_id=proof.target.revision_external_id,
                chunk_revision_external_id=proof.chunk_revision_external_id,
                chunk_content_sha256=proof.chunk_sha256,
                chunk_observation_id=proof.chunk_observation_id,
                index_observation_id=proof.index_observation_id,
                binding_revision=proof.binding_revision,
                issued_by_user_id=issuer_user_id,
                evidence_kind='physical_successor_preflight_v1',
                grants_admission=False,
            )
            db.add(record)
            db.flush()
            result = SuccessorCertificateIssueResult(record.id, created=True)
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
