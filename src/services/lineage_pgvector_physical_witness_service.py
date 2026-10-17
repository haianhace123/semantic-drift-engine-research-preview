"""Read-only, point-in-time PGVector chunk evidence; never an admission grant.

A trusted authority/operator must independently supply the expected identity and,
when content comparison is requested, a full SHA-256 of the *exact UTF-8 chunk
text* captured outside the inspected PGVector row. A hash derived from that
row or copied from its metadata is not independent evidence.

The result is a bounded, single-chunk physical observation only. It does not
attest an entire index, semantic correctness, downstream serving, durability,
or the source/trustworthiness of the supplied reference. No evidence is stored.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
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


MAX_WITNESS_CONTENT_BYTES = 1_048_576

# One SELECT statement gives a consistent PostgreSQL statement snapshot for
# collection identity, row identity, metadata and content. Bound inputs only.
# CASE prevents transferring an oversized document into the verifier.
_PHYSICAL_CHUNK_SQL = text(
    """
    SELECT collection.cmetadata,
           embedding.id,
           embedding.cmetadata,
           CASE
             WHEN collection.cmetadata ->> 'sde_organization_id' = :organization_id
              AND collection.cmetadata ->> 'sde_source_external_id' = :source_external_id
              AND collection.cmetadata ->> 'sde_index_external_id' = :index_external_id
              AND embedding.cmetadata ->> 'source_external_id' = :source_external_id
              AND embedding.cmetadata ->> 'chunk_revision_external_id' = :revision_external_id
              AND embedding.cmetadata ->> 'snapshot_external_id' = :snapshot_external_id
              AND embedding.document IS NOT NULL
              AND octet_length(embedding.document) <= :max_content_bytes
             THEN embedding.document
             ELSE NULL
           END,
           CASE
             WHEN collection.cmetadata ->> 'sde_organization_id' = :organization_id
              AND collection.cmetadata ->> 'sde_source_external_id' = :source_external_id
              AND collection.cmetadata ->> 'sde_index_external_id' = :index_external_id
              AND embedding.cmetadata ->> 'source_external_id' = :source_external_id
              AND embedding.cmetadata ->> 'chunk_revision_external_id' = :revision_external_id
              AND embedding.cmetadata ->> 'snapshot_external_id' = :snapshot_external_id
              AND embedding.document IS NOT NULL
             THEN octet_length(embedding.document)
             ELSE NULL
           END
    FROM langchain_pg_collection AS collection
    LEFT JOIN langchain_pg_embedding AS embedding
      ON embedding.collection_id = collection.uuid
     AND embedding.id = :document_id
    WHERE collection.name = :collection_name
    """
)


PhysicalWitnessStatus = Literal[
    "absent",
    "identity_mismatch",
    "content_unavailable",
    "identity_match_only",
    "digest_mismatch",
    "digest_match",
]


@dataclass(frozen=True, slots=True)
class PgVectorChunkWitness:
    status: PhysicalWitnessStatus
    code: str

    @property
    def matches_reference_content(self) -> bool:
        """True only for an explicit full-text digest comparison.

        This says nothing about the *independence* of the reference itself.
        """
        return self.status == "digest_match"


def _metadata(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(decoded, dict):
            return decoded
    return None


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty, canonical string.")
    return value


def inspect_pgvector_chunk_physical_witness(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_external_id: str,
    index_external_id: str,
    collection_name: str,
    chunk_external_id: str,
    chunk_revision_external_id: str,
    snapshot_external_id: str,
    expected_content_sha256: str | None = None,
) -> PgVectorChunkWitness:
    """Independently read a *physical row* against supplied reference facts.

    Uses only a parameterized SELECT. No network/DB session is created and no
    transaction is committed. Caller must use a trusted connection registry
    and close/rollback its read transaction. The digest reference must come
    from outside the PGVector row; this function cannot prove its origin.

    `digest_match` is NOT recovery authorization, complete-index proof, or
    a promotion of the existing producer-attested lineage `verified` state.
    """
    if not isinstance(organization_id, uuid.UUID):
        raise TypeError("organization_id must be a UUID.")
    source = _required(source_external_id, "source_external_id")
    index = _required(index_external_id, "index_external_id")
    collection = _required(collection_name, "collection_name")
    chunk = _required(chunk_external_id, "chunk_external_id")
    revision = _required(chunk_revision_external_id, "chunk_revision_external_id")
    snapshot = _required(snapshot_external_id, "snapshot_external_id")
    if expected_content_sha256 is not None and not re.fullmatch(
        r"[0-9a-f]{64}", expected_content_sha256
    ):
        raise ValueError("expected_content_sha256 must be 64 lowercase hex characters.")

    row = db.execute(
        _PHYSICAL_CHUNK_SQL,
        {
            "organization_id": str(organization_id),
            "source_external_id": source,
            "index_external_id": index,
            "revision_external_id": revision,
            "snapshot_external_id": snapshot,
            "collection_name": collection,
            "document_id": chunk,
            "max_content_bytes": MAX_WITNESS_CONTENT_BYTES,
        },
    ).one_or_none()
    if row is None:
        return PgVectorChunkWitness("absent", "collection_absent")

    collection_metadata = _metadata(row[0])
    if collection_metadata is None or (
        collection_metadata.get(SDE_ORGANIZATION_ID_KEY) != str(organization_id)
        or collection_metadata.get(SDE_SOURCE_EXTERNAL_ID_KEY) != source
        or collection_metadata.get(SDE_INDEX_EXTERNAL_ID_KEY) != index
    ):
        return PgVectorChunkWitness("identity_mismatch", "collection_identity_mismatch")

    if row[1] is None:
        return PgVectorChunkWitness("absent", "chunk_absent")
    if row[1] != chunk:
        return PgVectorChunkWitness("identity_mismatch", "chunk_identity_mismatch")

    chunk_metadata = _metadata(row[2])
    if chunk_metadata is None or (
        chunk_metadata.get("source_external_id") != source
        or chunk_metadata.get("chunk_revision_external_id") != revision
        or chunk_metadata.get("snapshot_external_id") != snapshot
    ):
        return PgVectorChunkWitness("identity_mismatch", "chunk_provenance_mismatch")

    content = row[3]
    content_bytes = row[4]
    if (
        not isinstance(content, str)
        or not isinstance(content_bytes, int)
        or content_bytes < 1
        or content_bytes > MAX_WITNESS_CONTENT_BYTES
    ):
        return PgVectorChunkWitness("content_unavailable", "content_missing_or_oversized")

    # An exact byte limit applies to the UTF-8 document representation used by
    # the existing LangChain chunk digest convention. No normalization.
    encoded = content.encode("utf-8")
    if len(encoded) != content_bytes:
        return PgVectorChunkWitness("content_unavailable", "content_encoding_mismatch")
    if expected_content_sha256 is None:
        return PgVectorChunkWitness("identity_match_only", "reference_digest_not_supplied")

    actual = hashlib.sha256(encoded).hexdigest()
    if not hmac.compare_digest(actual, expected_content_sha256):
        return PgVectorChunkWitness("digest_mismatch", "content_digest_mismatch")
    return PgVectorChunkWitness("digest_match", "content_matches_supplied_digest")
