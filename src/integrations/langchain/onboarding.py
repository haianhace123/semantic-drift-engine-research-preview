"""In-process LangChain indexing helpers for trusted index writers.

Physical PGVector writes and the SDE authority DB are separate transactions.
The caller owns authority commit/rollback and failure reconciliation.
This module never creates invalidation decisions or execution intents.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, Sequence, TYPE_CHECKING

from src.domain.lineage_execution_target import (
    LineageExecutionTargetBinding,
    LineageExecutionTargetIdentity,
)
from src.integrations.langchain.physical_readback import (
    ensure_physical_readback_available,
    snapshot_physical_readback_expectations,
    verify_physical_readback,
)
from src.integrations.langchain.lineage import (
    LangChainDocumentLike,
    LangChainIndexMaterialization,
    capture_langchain_index_materialization,
    prepare_langchain_documents_for_indexing,
)
if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from src.services.lineage_execution_target_binding_service import (
        PersistedLineageExecutionTargetBinding,
    )


def _stage_materialization(db: Session, *, organization_id: uuid.UUID, observation):
    # Avoid importing the SDE database engine simply to use the LangChain adapter.
    from src.services.lineage_observation_persistence_service import (
        persist_lineage_observation,
    )
    return persist_lineage_observation(
        db, organization_id=organization_id, observation=observation,
    )


def _stage_execution_binding(db: Session, *, organization_id: uuid.UUID, binding):
    from src.services.lineage_execution_target_binding_service import (
        persist_lineage_execution_target_binding,
    )
    return persist_lineage_execution_target_binding(
        db, organization_id=organization_id, binding=binding,
    )


class LangChainDocumentWriter(Protocol):
    """Subset of LangChain VectorStore needed for synchronous PGVector writes."""

    def add_documents(
        self,
        documents: list[LangChainDocumentLike],
        *,
        ids: list[str],
    ) -> list[str]: ...


class LangChainPhysicalWriteMismatchError(RuntimeError):
    """PGVector did not acknowledge every requested physical chunk ID."""


@dataclass(frozen=True)
class LangChainStagedIndexWrite:
    document_ids: tuple[str, ...]
    materialization: LangChainIndexMaterialization
    point_in_time_readback_verified: bool = False


def index_langchain_documents_with_lineage(
    *,
    vector_store: LangChainDocumentWriter,
    authority_db: Session,
    organization_id: uuid.UUID,
    source_external_id: str,
    documents: Sequence[LangChainDocumentLike],
    index_external_id: str,
    index_revision_external_id: str,
    sequence: int,
    previous_documents: Sequence[LangChainDocumentLike] = (),
    require_physical_readback: bool = True,
) -> LangChainStagedIndexWrite:
    """Write chunks before staging the corresponding SDE materialization.

    Strict readback is enabled by default: publish only after PGVector
    acknowledges exact IDs and a collection-scoped readback matches
    captured bytes and provenance. Legacy ACK-only mode requires an
    explicit require_physical_readback=False opt-out.

    Validate the full batch before physical I/O. Publish observations only
    after PGVector acknowledges the expected IDs. Does NOT commit the
    authority transaction. If downstream write or authority publication
    fails, the caller must reconcile the two stores before retrying.
    """
    if type(require_physical_readback) is not bool:
        raise TypeError("require_physical_readback must be a boolean.")
    if require_physical_readback:
        ensure_physical_readback_available(vector_store)
    if not isinstance(organization_id, uuid.UUID):
        raise TypeError("organization_id must be a UUID.")
    if not isinstance(source_external_id, str) or not source_external_id.strip():
        raise ValueError("source_external_id is required.")

    prepared = prepare_langchain_documents_for_indexing(
        documents=documents,
        previous_documents=previous_documents,
    )
    materialization = capture_langchain_index_materialization(
        documents=prepared.documents,
        index_external_id=index_external_id,
        index_revision_external_id=index_revision_external_id,
        sequence=sequence,
    )
    if materialization.observations[-1].source_external_id != source_external_id.strip():
        raise ValueError("Document source identity differs from the index source.")

    expected_readback = (
        snapshot_physical_readback_expectations(prepared.documents)
        if require_physical_readback else None
    )
    expected_ids = tuple(item.asset_external_id for item in prepared.provenance)
    acknowledged = vector_store.add_documents(
        list(prepared.documents),
        ids=list(expected_ids),
    )
    # Never publish authority evidence for a partial or redirected write.
    if (
        not isinstance(acknowledged, (list, tuple))
        or len(acknowledged) != len(expected_ids)
        or any(not isinstance(item, str) for item in acknowledged)
        or set(acknowledged) != set(expected_ids)
    ):
        raise LangChainPhysicalWriteMismatchError(
            "PGVector did not acknowledge the exact requested chunk identities. "
            "Physical writes may be partial; reconcile before retrying."
        )

    if expected_readback is not None:
        verify_physical_readback(vector_store, expected=expected_readback)

    for observation in materialization.observations:
        _stage_materialization(
            authority_db,
            organization_id=organization_id,
            observation=observation,
        )
    return LangChainStagedIndexWrite(
        document_ids=expected_ids,
        materialization=materialization,
        point_in_time_readback_verified=expected_readback is not None,
    )


def register_langchain_pgvector_causal_target(
    *,
    authority_db: Session,
    organization_id: uuid.UUID,
    source_external_id: str,
    index_external_id: str,
    connection_ref: str,
    collection_name: str,
    binding_revision: int,
) -> PersistedLineageExecutionTargetBinding:
    """Stage an immutable causal-chunk binding, never execution authority.

    The downstream collection must have been independently provisioned and
    verified. The caller owns the authority transaction and its commit.
    """
    if not isinstance(organization_id, uuid.UUID):
        raise TypeError("organization_id must be a UUID.")
    binding = LineageExecutionTargetBinding(
        identity=LineageExecutionTargetIdentity(
            source_external_id=source_external_id,
            asset_external_id=index_external_id,
        ),
        binding_revision=binding_revision,
        provider="pgvector",
        target_kind="langchain_pgvector_causal_chunk",
        configuration={
            "connection_ref": connection_ref,
            "collection_name": collection_name,
        },
    )
    return _stage_execution_binding(
        authority_db,
        organization_id=organization_id,
        binding=binding,
    )
