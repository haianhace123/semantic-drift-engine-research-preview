from __future__ import annotations

from typing import Mapping
import uuid

from sqlalchemy.orm import Session

from src.models.lineage_observation import (
    LineageObservationDependencyRecord,
    LineageObservationRecord,
)
from src.domain.lineage_revision import LineageRevisionIdentity

from src.domain.lineage_invalidation import LineageInvalidationDecision
from src.services.lineage_pgvector_invalidation_service import (
    LANGCHAIN_PGVECTOR_CAUSAL_CHUNK_TARGET_KIND,
    PGVECTOR_PROVIDER,
)


class LineageInvalidationExecutionTargetCausalMismatchError(RuntimeError):
    """The bound physical revision has no authority in the exact decision path."""


def resolve_causal_chunk_target_configuration(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision: LineageInvalidationDecision,
    provider: str,
    target_kind: str,
    configuration: Mapping[str, str],
) -> dict[str, str]:
    """Derive a physical chunk exclusively from tenant-scoped lineage evidence.

    Bindings may select a connection and a collection, never a row identity.
    Ambiguous paths or absent chunk materializations must fail closed.
    """
    if (
        provider != PGVECTOR_PROVIDER
        or target_kind != LANGCHAIN_PGVECTOR_CAUSAL_CHUNK_TARGET_KIND
    ):
        raise LineageInvalidationExecutionTargetCausalMismatchError(
            "Unsupported causal chunk execution target."
        )
    if set(configuration) != {"connection_ref", "collection_name"}:
        raise LineageInvalidationExecutionTargetCausalMismatchError(
            "Causal chunk binding must specify only a connection and collection."
        )
    collection_name = configuration.get("collection_name")
    connection_ref = configuration.get("connection_ref")
    if (
        not isinstance(collection_name, str)
        or not collection_name.strip()
        or not isinstance(connection_ref, str)
        or not connection_ref.strip()
    ):
        raise LineageInvalidationExecutionTargetCausalMismatchError(
            "Causal chunk binding requires a non-empty connection and collection."
        )

    # A stored decision is not, by itself, proof of a recorded dependency.
    for upstream, downstream in zip(
        decision.causal_path, decision.causal_path[1:]
    ):
        if upstream.source_external_id != downstream.source_external_id:
            raise LineageInvalidationExecutionTargetCausalMismatchError(
                "Cross-source dependency cannot be verified by lineage evidence."
            )
        edge_query = (
            db.query(LineageObservationRecord.id)
            .join(
                LineageObservationDependencyRecord,
                LineageObservationDependencyRecord.observation_id
                == LineageObservationRecord.id,
            )
            .filter(
                LineageObservationRecord.organization_id == organization_id,
                LineageObservationRecord.source_external_id
                == downstream.source_external_id,
                LineageObservationRecord.asset_external_id
                == downstream.asset_external_id,
                LineageObservationRecord.revision_external_id
                == downstream.revision_external_id,
                LineageObservationDependencyRecord.asset_external_id
                == upstream.asset_external_id,
                LineageObservationDependencyRecord.revision_external_id
                == upstream.revision_external_id,
            )
        )
        if downstream == decision.target:
            # A consumption of the index is not proof that a chunk was
            # materialized into its physical PGVector collection.
            edge_query = edge_query.filter(
                LineageObservationRecord.observation_kind == "materialization",
                LineageObservationRecord.asset_kind == "index",
            )
        has_edge = edge_query.first()
        if has_edge is None:
            raise LineageInvalidationExecutionTargetCausalMismatchError(
                "Decision path lacks a recorded exact lineage dependency."
            )

    chunks: set[LineageRevisionIdentity] = set()
    for node in decision.causal_path:
        exists = (
            db.query(LineageObservationRecord.id)
            .filter(
                LineageObservationRecord.organization_id == organization_id,
                LineageObservationRecord.observation_kind == "materialization",
                LineageObservationRecord.asset_kind == "chunk",
                LineageObservationRecord.source_external_id == node.source_external_id,
                LineageObservationRecord.asset_external_id == node.asset_external_id,
                LineageObservationRecord.revision_external_id == node.revision_external_id,
            )
            .first()
        )
        if exists is not None:
            chunks.add(node)

    if len(chunks) != 1:
        raise LineageInvalidationExecutionTargetCausalMismatchError(
            "Causal chunk execution requires exactly one evidenced chunk "
            "revision on the authorized decision path."
        )
    chunk = next(iter(chunks))
    return {
        "organization_id": str(organization_id),
        "index_external_id": decision.target.asset_external_id,
        "collection_name": collection_name.strip(),
        "document_id": chunk.asset_external_id,
        "document_revision_external_id": chunk.revision_external_id,
        "source_external_id": chunk.source_external_id,
    }
