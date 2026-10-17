from __future__ import annotations

import uuid


# Collection-level identity is written when the trusted materializer creates
# the PGVector collection, independently of the execution target binding.
SDE_ORGANIZATION_ID_KEY = "sde_organization_id"
SDE_SOURCE_EXTERNAL_ID_KEY = "sde_source_external_id"
SDE_INDEX_EXTERNAL_ID_KEY = "sde_index_external_id"


def sde_pgvector_collection_metadata(
    *,
    organization_id: uuid.UUID,
    source_external_id: str,
    index_external_id: str,
) -> dict[str, str]:
    """Metadata passed as PGVector(collection_metadata=...) at index creation.

    A shared collection cannot be used for multiple SDE index identities.
    """
    if not isinstance(organization_id, uuid.UUID):
        raise TypeError("organization_id must be a UUID.")
    if not isinstance(source_external_id, str) or not source_external_id.strip():
        raise ValueError("source_external_id must be a non-empty string.")
    if not isinstance(index_external_id, str) or not index_external_id.strip():
        raise ValueError("index_external_id must be a non-empty string.")
    return {
        SDE_ORGANIZATION_ID_KEY: str(organization_id),
        SDE_SOURCE_EXTERNAL_ID_KEY: source_external_id.strip(),
        SDE_INDEX_EXTERNAL_ID_KEY: index_external_id.strip(),
    }
