"""Fail-closed consumer boundary for one canonical pilot recovery chunk.

The caller owns an authenticated, trusted HTTP client. This adapter freezes
retrieved UTF-8 content before admission and returns only the frozen copy.
It does not modify normal LangChain integrity policy or issue certificates.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass

ROUTE = "/api/lineage/recovery-context-admission/query"
MAX_CONTENT_BYTES = 1_048_576
BLOCK_REASONS = frozenset({
    "unsupported", "invalid_context", "denied",
    "not_current", "evidence_unavailable", "verification_failed",
})


class PilotRecoveryContextNotAdmittedError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"SDE pilot recovery admission blocked: {reason}")


@dataclass(frozen=True, slots=True)
class AdmittedPilotRecoveryContext:
    source_id: uuid.UUID
    snapshot_id: uuid.UUID
    chunk_external_id: str
    chunk_revision_external_id: str
    index_revision_external_id: str
    content: str
    content_sha256: str
    certificate_id: uuid.UUID


def _block(reason: str) -> None:
    raise PilotRecoveryContextNotAdmittedError(reason)


def admit_pilot_recovery_document(
    *,
    http_client: object,
    source_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    retrieved_document: object,
) -> AdmittedPilotRecoveryContext:
    """Admit exactly one frozen canonical pilot chunk for this request.

    http_client must be configured by the trusted consumer with an
    authenticated SDE endpoint and secure transport. Never reuse this
    result as a serving grant for a later request or different document.
    """
    if not isinstance(source_id, uuid.UUID) or not isinstance(
        snapshot_id, uuid.UUID
    ):
        _block("invalid_context")

    chunk_id = f"sde-pilot-chunk:{source_id}:0"
    source_external_id = f"sde-pilot-source:{source_id}"
    index_revision = f"sde-pilot-index:{source_id}@{snapshot_id}"

    document_id = getattr(retrieved_document, "id", None)
    content = getattr(retrieved_document, "page_content", None)
    metadata = getattr(retrieved_document, "metadata", None)

    if (
        document_id != chunk_id
        or not isinstance(content, str)
        or not isinstance(metadata, dict)
    ):
        _block("invalid_context")

    try:
        frozen_bytes = content.encode("utf-8", errors="strict")
    except UnicodeError:
        _block("invalid_context")

    if not 0 < len(frozen_bytes) <= MAX_CONTENT_BYTES:
        _block("invalid_context")

    digest = hashlib.sha256(frozen_bytes).hexdigest()
    chunk_revision = f"{chunk_id}:{digest[:16]}"

    if (
        metadata.get("source_external_id") != source_external_id
        or metadata.get("snapshot_external_id") != str(snapshot_id)
        or metadata.get("chunk_revision_external_id") != chunk_revision
    ):
        _block("invalid_context")

    payload = {
        "source_id": str(source_id),
        "snapshot_id": str(snapshot_id),
        "chunk_external_id": chunk_id,
        "chunk_revision_external_id": chunk_revision,
        "index_revision_external_id": index_revision,
        "frozen_content_sha256": digest,
    }

    try:
        response = http_client.post(ROUTE, json=payload)
    except Exception:
        _block("verification_failed")

    if getattr(response, "status_code", None) in (401, 403):
        _block("denied")
    if getattr(response, "status_code", None) != 200:
        _block("verification_failed")

    try:
        cache_control = response.headers.get("cache-control", "").lower()
        result = response.json()
    except Exception:
        _block("verification_failed")

    if "no-store" not in cache_control or not isinstance(result, dict):
        _block("verification_failed")

    if result.get("outcome") == "block":
        reason = result.get("reason")
        _block(reason if reason in BLOCK_REASONS else "verification_failed")

    if (
        result.get("outcome") != "proceed"
        or result.get("reason") != "verified_successor"
        or result.get("request_scoped") is not True
        or result.get("snapshot_id") != str(snapshot_id)
        or not isinstance(result.get("chunk_content_sha256"), str)
        or not hmac.compare_digest(
            result["chunk_content_sha256"], digest
        )
        or not isinstance(result.get("certificate_id"), str)
    ):
        _block("verification_failed")

    try:
        certificate_id = uuid.UUID(result["certificate_id"])
    except (ValueError, TypeError, AttributeError):
        _block("verification_failed")

    return AdmittedPilotRecoveryContext(
        source_id=source_id,
        snapshot_id=snapshot_id,
        chunk_external_id=chunk_id,
        chunk_revision_external_id=chunk_revision,
        index_revision_external_id=index_revision,
        content=content,
        content_sha256=digest,
        certificate_id=certificate_id,
    )
