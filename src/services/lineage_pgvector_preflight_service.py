"""Read-only physical readiness check for a PGVector causal-chunk target.

This is an onboarding diagnostic, not an authorization or execution gate.
Execution must independently revalidate causal evidence, collection ownership,
and the locked embedding row.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.domain.lineage_pgvector_collection import (
    SDE_INDEX_EXTERNAL_ID_KEY,
    SDE_ORGANIZATION_ID_KEY,
    SDE_SOURCE_EXTERNAL_ID_KEY,
)


_COLLECTION_SQL = text(
    """SELECT uuid, cmetadata
       FROM langchain_pg_collection
       WHERE name = :collection_name"""
)
_CHUNK_SQL = text(
    """SELECT cmetadata
       FROM langchain_pg_embedding
       WHERE collection_id = :collection_id AND id = :chunk_id"""
)


@dataclass(frozen=True, slots=True)
class PgVectorPreflightResult:
    status: Literal["ready", "not_ready"]
    code: str
    detail: str

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, str]:
        return {"status": self.status, "code": self.code, "detail": self.detail}


def _metadata_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")
    return value.strip()


def inspect_pgvector_causal_target(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_external_id: str,
    index_external_id: str,
    collection_name: str,
    chunk_id: str | None = None,
    chunk_revision_external_id: str | None = None,
) -> PgVectorPreflightResult:
    """Check physical collection identity and optional exact chunk provenance.

    Executes only parameterized SELECTs. A READY result establishes physical
    readiness at inspection time; it does not prove lineage authorization,
    current revision pinning, or downstream integrity verification.
    """
    if not isinstance(organization_id, uuid.UUID):
        raise TypeError("organization_id must be a UUID.")
    source = _required_text(source_external_id, "source_external_id")
    index = _required_text(index_external_id, "index_external_id")
    collection = _required_text(collection_name, "collection_name")
    if (chunk_id is None) != (chunk_revision_external_id is None):
        raise ValueError("chunk_id and chunk_revision_external_id must be specified together.")
    if chunk_id is not None:
        chunk_id = _required_text(chunk_id, "chunk_id")
        chunk_revision_external_id = _required_text(
            chunk_revision_external_id, "chunk_revision_external_id"
        )

    row = db.execute(_COLLECTION_SQL, {"collection_name": collection}).one_or_none()
    if row is None:
        return PgVectorPreflightResult(
            "not_ready", "collection_not_found",
            "Collection is absent. Provision it with trusted identity metadata first.",
        )

    identity = _metadata_object(row[1])
    required = (SDE_ORGANIZATION_ID_KEY, SDE_SOURCE_EXTERNAL_ID_KEY, SDE_INDEX_EXTERNAL_ID_KEY)
    if any(not isinstance(identity.get(key), str) or not identity[key] for key in required):
        return PgVectorPreflightResult(
            "not_ready", "collection_identity_missing",
            "Collection identity is missing or malformed. Verify ownership before provisioning tags.",
        )
    if (
        identity[SDE_ORGANIZATION_ID_KEY] != str(organization_id)
        or identity[SDE_SOURCE_EXTERNAL_ID_KEY] != source
        or identity[SDE_INDEX_EXTERNAL_ID_KEY] != index
    ):
        return PgVectorPreflightResult(
            "not_ready", "collection_identity_mismatch",
            "Collection belongs to a different tenant, source, or index. Check the binding; do not retag it.",
        )

    if chunk_id is None:
        return PgVectorPreflightResult(
            "ready", "collection_ready",
            "Collection identity matches. Chunk and lineage evidence were not inspected.",
        )

    physical = db.execute(
        _CHUNK_SQL, {"collection_id": row[0], "chunk_id": chunk_id}
    ).one_or_none()
    if physical is None:
        return PgVectorPreflightResult(
            "not_ready", "chunk_not_found",
            "Chunk ID is absent from this collection. Check LangChain document IDs and the index write.",
        )
    metadata = _metadata_object(physical[0])
    if (
        metadata.get("source_external_id") != source
        or metadata.get("chunk_revision_external_id") != chunk_revision_external_id
    ):
        return PgVectorPreflightResult(
            "not_ready", "chunk_provenance_mismatch",
            "Physical chunk source or revision differs from the expected provenance.",
        )
    return PgVectorPreflightResult(
        "ready", "chunk_ready",
        "Collection and the sampled chunk have matching physical identity. Causal authority is not verified.",
    )
