from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import text
from sqlalchemy.orm import Session



PGVECTOR_PROVIDER = "pgvector"
LANGCHAIN_PGVECTOR_DOCUMENT_TARGET_KIND = "langchain_pgvector_document"
LANGCHAIN_PGVECTOR_DOCUMENT_REVISION_TARGET_KIND = (
    "langchain_pgvector_document_revision"
)
LANGCHAIN_PGVECTOR_CAUSAL_CHUNK_TARGET_KIND = "langchain_pgvector_causal_chunk"

_COLLECTION_NAME_CONFIGURATION_KEY = "collection_name"
_DOCUMENT_ID_CONFIGURATION_KEY = "document_id"
_DOCUMENT_REVISION_EXTERNAL_ID_CONFIGURATION_KEY = (
    "document_revision_external_id"
)

_DELETE_DOCUMENT_SQL = text(
    """
    DELETE FROM langchain_pg_embedding AS embedding
    USING langchain_pg_collection AS collection
    WHERE embedding.collection_id = collection.uuid
      AND collection.name = :collection_name
      AND embedding.id = :document_id
    """
)

_LOCK_COLLECTION_IDENTITY_SQL = text(
    """
    SELECT collection.uuid,
           collection.cmetadata ->> 'sde_organization_id',
           collection.cmetadata ->> 'sde_source_external_id',
           collection.cmetadata ->> 'sde_index_external_id'
    FROM langchain_pg_collection AS collection
    WHERE collection.name = :collection_name
    FOR SHARE OF collection
    """
)

_LOCK_DOCUMENT_PROVENANCE_SQL = text(
    """
    SELECT embedding.cmetadata ->> 'source_external_id',
           embedding.cmetadata ->> 'chunk_revision_external_id'
    FROM langchain_pg_embedding AS embedding
    WHERE embedding.collection_id = :collection_id
      AND embedding.id = :document_id
    FOR UPDATE OF embedding
    """
)

_DELETE_CAUSAL_CHUNK_SQL = text(
    """
    DELETE FROM langchain_pg_embedding
    WHERE collection_id = :collection_id
      AND id = :document_id
    """
)

_LOCK_DOCUMENT_REVISION_SQL = text(
    """
    SELECT embedding.cmetadata ->> 'chunk_revision_external_id'
    FROM langchain_pg_embedding AS embedding
    JOIN langchain_pg_collection AS collection
      ON collection.uuid = embedding.collection_id
    WHERE collection.name = :collection_name
      AND embedding.id = :document_id
    FOR UPDATE OF embedding
    """
)


@dataclass(frozen=True, slots=True)
class LineagePgVectorInvalidationResult:
    collection_name: str
    document_id: str
    deleted: bool


class LineagePgVectorInvalidationError(RuntimeError):
    """Base error for PGVector invalidation failures."""


class LineagePgVectorInvalidationConfigurationError(
    LineagePgVectorInvalidationError
):
    """Raised when PGVector target configuration is invalid."""



class LineagePgVectorInvalidationRevisionConflictError(
    LineagePgVectorInvalidationError
):
    """Raised when a physical document has advanced to another revision."""


def invalidate_lineage_pgvector_document_revision(
    db: Session,
    *,
    configuration: Mapping[str, str],
) -> LineagePgVectorInvalidationResult:
    """
    Invalidate one exact LangChain PGVector document revision.

    The physical row is locked before its persisted revision metadata is
    checked. An already-absent document is an idempotent success. A row
    that now represents another revision fails closed and is not deleted.

    The caller owns commit / rollback.
    """
    normalized_configuration = dict(configuration)

    collection_name = _required_configuration_value(
        normalized_configuration,
        key=_COLLECTION_NAME_CONFIGURATION_KEY,
    )
    document_id = _required_configuration_value(
        normalized_configuration,
        key=_DOCUMENT_ID_CONFIGURATION_KEY,
    )
    document_revision_external_id = _required_configuration_value(
        normalized_configuration,
        key=_DOCUMENT_REVISION_EXTERNAL_ID_CONFIGURATION_KEY,
    )

    unexpected_keys = set(normalized_configuration) - {
        _COLLECTION_NAME_CONFIGURATION_KEY,
        _DOCUMENT_ID_CONFIGURATION_KEY,
        _DOCUMENT_REVISION_EXTERNAL_ID_CONFIGURATION_KEY,
    }

    if unexpected_keys:
        raise LineagePgVectorInvalidationConfigurationError(
            "PGVector document revision target configuration contains "
            "unsupported keys."
        )

    persisted_revision_row = db.execute(
        _LOCK_DOCUMENT_REVISION_SQL,
        {
            "collection_name": collection_name,
            "document_id": document_id,
        },
    ).one_or_none()

    if persisted_revision_row is None:
        return LineagePgVectorInvalidationResult(
            collection_name=collection_name,
            document_id=document_id,
            deleted=False,
        )

    persisted_revision_external_id = persisted_revision_row[0]

    if persisted_revision_external_id != document_revision_external_id:
        raise LineagePgVectorInvalidationRevisionConflictError(
            "PGVector document revision no longer matches the pinned "
            "execution target."
        )

    result = db.execute(
        _DELETE_DOCUMENT_SQL,
        {
            "collection_name": collection_name,
            "document_id": document_id,
        },
    )

    if result.rowcount != 1:
        raise LineagePgVectorInvalidationError(
            "Locked PGVector document disappeared before invalidation."
        )

    return LineagePgVectorInvalidationResult(
        collection_name=collection_name,
        document_id=document_id,
        deleted=True,
    )


class LineagePgVectorInvalidationProvenanceConflictError(
    LineagePgVectorInvalidationError
):
    """A physical chunk does not match the authoritative source and revision."""


class LineagePgVectorInvalidationPhysicalConfirmationError(
    LineagePgVectorInvalidationError
):
    """The exact invalidated revision is still physically present."""


def invalidate_lineage_pgvector_causal_chunk(
    db: Session,
    *,
    configuration: Mapping[str, str],
) -> LineagePgVectorInvalidationResult:
    """Delete one authority-derived chunk only when its physical provenance matches.

    The caller must supply identity from the recorded causal path, never from
    the execution binding. A missing row is an idempotent success. The row
    lock prevents changing the metadata between verification and deletion.
    """
    normalized = dict(configuration)
    required = {
        "organization_id",
        "index_external_id",
        "collection_name",
        "document_id",
        "document_revision_external_id",
        "source_external_id",
    }
    if set(normalized) != required:
        raise LineagePgVectorInvalidationConfigurationError(
            "Causal chunk execution requires tenant, source, index, "
            "collection, document identity and revision."
        )

    collection_name = _required_configuration_value(
        normalized, key="collection_name"
    )
    document_id = _required_configuration_value(normalized, key="document_id")
    revision = _required_configuration_value(
        normalized, key="document_revision_external_id"
    )
    source = _required_configuration_value(normalized, key="source_external_id")
    organization_id = _required_configuration_value(
        normalized, key="organization_id"
    )
    index_external_id = _required_configuration_value(
        normalized, key="index_external_id"
    )

    # Verify the physical collection identity BEFORE interpreting an absent
    # chunk as an idempotent success. Lock the collection to prevent metadata
    # reassignment until the deletion transaction commits or rolls back.
    # These tags must be written by a trusted index materializer, not copied
    # from execution binding configuration.
    collection = db.execute(
        _LOCK_COLLECTION_IDENTITY_SQL,
        {"collection_name": collection_name},
    ).one_or_none()
    if (
        collection is None
        or collection[1] != organization_id
        or collection[2] != source
        or collection[3] != index_external_id
    ):
        raise LineagePgVectorInvalidationProvenanceConflictError(
            "PGVector collection identity does not match the "
            "authorized tenant, source and index."
        )
    collection_id = collection[0]

    physical = db.execute(
        _LOCK_DOCUMENT_PROVENANCE_SQL,
        {"collection_id": collection_id, "document_id": document_id},
    ).one_or_none()
    if physical is None:
        return LineagePgVectorInvalidationResult(
            collection_name=collection_name,
            document_id=document_id,
            deleted=False,
        )
    if physical[0] != source or physical[1] != revision:
        raise LineagePgVectorInvalidationProvenanceConflictError(
            "Physical PGVector chunk source or revision does not match "
            "the authoritative lineage identity."
        )

    deleted = db.execute(
        _DELETE_CAUSAL_CHUNK_SQL,
        {"collection_id": collection_id, "document_id": document_id},
    )
    if deleted.rowcount != 1:
        raise LineagePgVectorInvalidationError(
            "Locked PGVector chunk disappeared before invalidation."
        )
    return LineagePgVectorInvalidationResult(
        collection_name=collection_name,
        document_id=document_id,
        deleted=True,
    )


def confirm_lineage_pgvector_causal_chunk_absent(
    db: Session,
    *,
    configuration: Mapping[str, str],
) -> None:
    """Confirm the pinned old revision is absent in the downstream database.

    Call in a NEW transaction after committing the physical invalidation.
    This rechecks trusted collection identity even when the row is absent.
    A newer revision for the same source and document ID is acceptable: it
    proves only that the pinned revision is no longer at that identity, not
    that a downstream rebuild or a remediation was independently verified.

    The caller must end the readback transaction to release database locks.
    """
    normalized = dict(configuration)
    required = {
        "organization_id",
        "index_external_id",
        "collection_name",
        "document_id",
        "document_revision_external_id",
        "source_external_id",
    }
    if set(normalized) != required:
        raise LineagePgVectorInvalidationConfigurationError(
            "Causal chunk confirmation requires exact physical provenance."
        )

    values = {
        key: _required_configuration_value(normalized, key=key)
        for key in required
    }
    collection = db.execute(
        _LOCK_COLLECTION_IDENTITY_SQL,
        {"collection_name": values["collection_name"]},
    ).one_or_none()
    if (
        collection is None
        or collection[1] != values["organization_id"]
        or collection[2] != values["source_external_id"]
        or collection[3] != values["index_external_id"]
    ):
        raise LineagePgVectorInvalidationProvenanceConflictError(
            "PGVector collection identity is not the authorized tenant, "
            "source and index during physical confirmation."
        )

    physical = db.execute(
        _LOCK_DOCUMENT_PROVENANCE_SQL,
        {
            "collection_id": collection[0],
            "document_id": values["document_id"],
        },
    ).one_or_none()
    if physical is None:
        return
    if physical[0] != values["source_external_id"]:
        raise LineagePgVectorInvalidationProvenanceConflictError(
            "Physical confirmation found a document with foreign source."
        )
    if (
        not isinstance(physical[1], str)
        or not physical[1].strip()
        or physical[1] != physical[1].strip()
    ):
        raise LineagePgVectorInvalidationProvenanceConflictError(
            "Physical confirmation found invalid document revision metadata."
        )
    if physical[1] == values["document_revision_external_id"]:
        raise LineagePgVectorInvalidationPhysicalConfirmationError(
            "Pinned PGVector chunk revision remains physically present "
            "after downstream commit."
        )


def _required_configuration_value(
    configuration: dict[str, str],
    *,
    key: str,
) -> str:
    value = configuration.get(key)

    if not isinstance(value, str):
        raise LineagePgVectorInvalidationConfigurationError(
            f"PGVector document target requires {key!r}."
        )

    normalized = value.strip()

    if not normalized:
        raise LineagePgVectorInvalidationConfigurationError(
            f"PGVector document target requires non-empty {key!r}."
        )

    return normalized