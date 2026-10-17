"""Request-bound, fail-closed recovery admission for the ONE pilot canonical chunk.

An A2 certificate is non-authorizing evidence. This service decides for one
frozen byte digest, without calling an LLM, changing normal unknown->block
semantics, or creating a reusable grant. Disabled by default. The caller must
obtain api_key_id from the authenticated server dependency, NEVER request JSON.

No cross-PostgreSQL transaction or writer lease is claimed. The customer must
freeze/use exactly the bytes whose SHA256 was submitted, without refetching a
mutable row after admission. Not a general customer vector store adapter.
"""
from __future__ import annotations

import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from sqlalchemy.orm import Session

from src.models.api_key import ApiKey
from src.models.monitored_page import MonitoredPage
from src.models.page_snapshot import PageSnapshot
from src.models.user import User
from src.models.lineage_successor_recovery_certificate import (
    LineageSuccessorRecoveryCertificateRecord,
)
from src.services.lineage_successor_recovery_preflight_service import (
    _AuthorityFacts,
    _authority_facts,
    _read_only,
    assess_verified_successor_recovery_candidate,
    SuccessorRecoveryPreflight,
)

_SHA = re.compile(r"[0-9a-f]{64}\Z")
Outcome = Literal["proceed", "block"]
Reason = Literal[
    "verified_successor", "unsupported", "invalid_context", "denied",
    "not_current", "evidence_unavailable", "verification_failed",
]


@dataclass(frozen=True, slots=True)
class FrozenSuccessorContext:
    """Data asserted by a consumer about its immutable retrieval for THIS call."""

    source_id: uuid.UUID
    snapshot_id: uuid.UUID
    chunk_external_id: str
    chunk_revision_external_id: str
    index_revision_external_id: str
    frozen_content_sha256: str


@dataclass(frozen=True, slots=True)
class RecoveryContextAdmissionDecision:
    outcome: Outcome
    reason: Reason
    # Non-null only on proceed. A certificate is NEVER a bearer token.
    snapshot_id: uuid.UUID | None = None
    certificate_id: uuid.UUID | None = None
    chunk_content_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class _AuthorityEvidence:
    facts: _AuthorityFacts
    source_url: str
    certificate_id: uuid.UUID
    certificate_digest: str
    certificate_identity: tuple


def _deny(reason: Reason) -> RecoveryContextAdmissionDecision:
    return RecoveryContextAdmissionDecision("block", reason)


def _is_valid_context(context: object) -> bool:
    if not isinstance(context, FrozenSuccessorContext):
        return False
    if not isinstance(context.source_id, uuid.UUID) or not isinstance(context.snapshot_id, uuid.UUID):
        return False
    if not isinstance(context.frozen_content_sha256, str) or not _SHA.fullmatch(context.frozen_content_sha256):
        return False
    return all(
        isinstance(value, str) and 0 < len(value) <= 512 and value.isascii()
        for value in (
            context.chunk_external_id, context.chunk_revision_external_id,
            context.index_revision_external_id,
        )
    )


def _active_reader(db: Session, *, organization_id: uuid.UUID, api_key_id: uuid.UUID) -> bool:
    key = (
        db.query(ApiKey)
        .filter(
            ApiKey.id == api_key_id,
            ApiKey.organization_id == organization_id,
            ApiKey.is_active.is_(True),
        )
        .one_or_none()
    )
    if key is None or not isinstance(key.permissions, dict) or key.permissions.get("read") is not True:
        return False
    expires = key.expires_at
    if expires is not None:
        if expires.tzinfo is None:
            return False
        if expires <= datetime.now(timezone.utc):
            return False
    return True


def _issuer_is_current_admin(db: Session, *, organization_id: uuid.UUID, issuer_id: uuid.UUID) -> bool:
    return (
        db.query(User)
        .filter(
            User.id == issuer_id,
            User.organization_id == organization_id,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            User.role == "admin",
        )
        .one_or_none()
        is not None
    )


def _current_authority(
    db: Session, *, organization_id: uuid.UUID, api_key_id: uuid.UUID,
    context: FrozenSuccessorContext, snapshot_root: Path,
) -> _AuthorityEvidence:
    if not _active_reader(db, organization_id=organization_id, api_key_id=api_key_id):
        raise PermissionError("reader_not_authorized")
    source = (
        db.query(MonitoredPage)
        .filter(
            MonitoredPage.id == context.source_id,
            MonitoredPage.organization_id == organization_id,
            MonitoredPage.is_active.is_(True),
        )
        .one_or_none()
    )
    if source is None:
        raise ValueError("source_unavailable")
    latest = (
        db.query(PageSnapshot)
        .filter(
            PageSnapshot.organization_id == organization_id,
            PageSnapshot.monitored_page_id == context.source_id,
        )
        .order_by(PageSnapshot.crawled_at.desc(), PageSnapshot.id.desc())
        .first()
    )
    if latest is None or latest.id != context.snapshot_id:
        raise ValueError("snapshot_not_current")
    facts = _authority_facts(
        db, organization_id=organization_id, source_id=context.source_id,
        source_url=source.url, snapshot_id=context.snapshot_id,
        snapshot_sha256=latest.content_hash, snapshot_root=snapshot_root,
    )
    ref = facts.reference
    if (
        ref.chunk_external_id != context.chunk_external_id
        or ref.chunk_revision_external_id != context.chunk_revision_external_id
        or facts.target.revision_external_id != context.index_revision_external_id
        or not hmac.compare_digest(ref.chunk_content_sha256, context.frozen_content_sha256)
    ):
        raise ValueError("frozen_revision_or_digest_mismatch")
    cert = (
        db.query(LineageSuccessorRecoveryCertificateRecord)
        .filter(
            LineageSuccessorRecoveryCertificateRecord.organization_id == organization_id,
            LineageSuccessorRecoveryCertificateRecord.source_id == context.source_id,
            LineageSuccessorRecoveryCertificateRecord.snapshot_id == context.snapshot_id,
            LineageSuccessorRecoveryCertificateRecord.target_source_external_id == facts.target.source_external_id,
            LineageSuccessorRecoveryCertificateRecord.target_asset_external_id == facts.target.asset_external_id,
            LineageSuccessorRecoveryCertificateRecord.target_revision_external_id == facts.target.revision_external_id,
        )
        .one_or_none()
    )
    if cert is None or not _issuer_is_current_admin(
        db, organization_id=organization_id, issuer_id=cert.issued_by_user_id,
    ):
        raise ValueError("certificate_missing_or_issuer_revoked")
    identity = (
        cert.id, cert.organization_id, cert.source_id, cert.snapshot_id,
        cert.snapshot_sha256, cert.target_source_external_id,
        cert.target_asset_external_id, cert.target_revision_external_id,
        cert.chunk_revision_external_id, cert.chunk_content_sha256,
        cert.chunk_observation_id, cert.index_observation_id,
        int(cert.binding_revision), cert.issued_by_user_id,
        cert.evidence_kind, cert.grants_admission,
    )
    if (
        cert.organization_id != organization_id or cert.source_id != context.source_id
        or cert.snapshot_id != context.snapshot_id
        or cert.snapshot_sha256 != ref.snapshot_sha256
        or cert.target_source_external_id != facts.target.source_external_id
        or cert.target_asset_external_id != facts.target.asset_external_id
        or cert.target_revision_external_id != facts.target.revision_external_id
        or cert.chunk_revision_external_id != ref.chunk_revision_external_id
        or cert.chunk_content_sha256 != ref.chunk_content_sha256
        or cert.chunk_observation_id != facts.chunk_observation_id
        or cert.index_observation_id != facts.index_observation_id
        or int(cert.binding_revision) != facts.binding_revision
        or cert.evidence_kind != "physical_successor_preflight_v1"
        or cert.grants_admission is not False
    ):
        raise ValueError("certificate_authority_mismatch")
    return _AuthorityEvidence(facts, source.url, cert.id, cert.chunk_content_sha256, identity)


def _physical_matches(
    proof: SuccessorRecoveryPreflight, authority: _AuthorityEvidence,
    organization_id: uuid.UUID, context: FrozenSuccessorContext,
) -> bool:
    facts = authority.facts
    return (
        proof.grants_admission is False
        and proof.witness_status == "digest_match"
        and proof.organization_id == organization_id
        and proof.source_id == context.source_id
        and proof.snapshot_id == context.snapshot_id
        and proof.target == facts.target
        and proof.chunk_revision_external_id == context.chunk_revision_external_id
        and proof.chunk_sha256 == context.frozen_content_sha256
        and proof.chunk_observation_id == facts.chunk_observation_id
        and proof.index_observation_id == facts.index_observation_id
        and proof.binding_revision == facts.binding_revision
    )


def decide_recovery_context_admission(
    *, enabled: bool = False, authority_session_factory: Callable[[], Session],
    snapshot_root: Path, organization_id: uuid.UUID, authenticated_api_key_id: uuid.UUID,
    context: FrozenSuccessorContext,
) -> RecoveryContextAdmissionDecision:
    """Return one request-scoped verdict; never invokes a model or writes a grant.

    This is a specialized single-chunk capability. The API key ID and tenant
    MUST be supplied by a trusted API authentication layer, not client JSON.
    All unverified states fail closed with non-sensitive reason codes.
    """
    if not enabled:
        return _deny("unsupported")
    if (
        not isinstance(organization_id, uuid.UUID)
        or not isinstance(authenticated_api_key_id, uuid.UUID)
        or not isinstance(snapshot_root, Path)
        or not _is_valid_context(context)
    ):
        return _deny("invalid_context")
    if not callable(authority_session_factory):
        return _deny("verification_failed")
    params = dict(
        organization_id=organization_id, api_key_id=authenticated_api_key_id,
        context=context, snapshot_root=snapshot_root,
    )
    try:
        before = _read_only(
            authority_session_factory, lambda db: _current_authority(db, **params),
        )
        ref = before.facts.reference
        # A1 resolves only the trusted, tenant-scoped current downstream binding.
        # Neither request JSON nor the certificate supplies a DB connection URL.
        proof = assess_verified_successor_recovery_candidate(
            authority_session_factory=authority_session_factory,
            organization_id=organization_id, source_id=context.source_id,
            expected_source_url=before.source_url,
            expected_snapshot_id=context.snapshot_id,
            expected_snapshot_sha256=ref.snapshot_sha256,
            snapshot_root=snapshot_root,
        )
        if not _physical_matches(proof, before, organization_id, context):
            return _deny("verification_failed")
        # NEW transaction after PGVector read: fences observable authority,
        # key revocation, issuer revocation and certificate changes.
        after = _read_only(
            authority_session_factory, lambda db: _current_authority(db, **params),
        )
        if after != before:
            return _deny("not_current")
        return RecoveryContextAdmissionDecision(
            "proceed", "verified_successor", snapshot_id=context.snapshot_id,
            certificate_id=after.certificate_id,
            chunk_content_sha256=context.frozen_content_sha256,
        )
    except PermissionError:
        return _deny("denied")
    except ValueError:
        return _deny("evidence_unavailable")
    except Exception:
        # Private DB exception details and other tenant identities never escape.
        return _deny("verification_failed")

