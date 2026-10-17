"""Remote LangChain index publication via the authenticated SDE HTTP API.

The physical PGVector write and each HTTP publication are independently committed.
Never interpret successful publication as permission to execute invalidation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from src.domain.lineage_observation import LineageObservation
from src.integrations.integrity_client import LineageObservationPublication
from src.integrations.langchain.lineage import (
    LangChainDocumentLike,
    LangChainIndexMaterialization,
    capture_langchain_index_materialization,
    prepare_langchain_documents_for_indexing,
)
from src.integrations.langchain.physical_readback import (
    ensure_physical_readback_available,
    snapshot_physical_readback_expectations,
    verify_physical_readback,
)
from src.integrations.langchain.onboarding import (
    LangChainDocumentWriter,
    LangChainPhysicalWriteMismatchError,
)


class RemoteLineagePublisher(Protocol):
    def publish_lineage_observation(
        self, observation: LineageObservation
    ) -> LineageObservationPublication: ...


@dataclass(frozen=True, slots=True)
class RemoteLangChainIndexWrite:
    document_ids: tuple[str, ...]
    materialization: LangChainIndexMaterialization
    publications: tuple[LineageObservationPublication, ...]
    point_in_time_readback_verified: bool = False


def index_langchain_documents_with_remote_lineage(
    *,
    vector_store: LangChainDocumentWriter,
    lineage_client: RemoteLineagePublisher,
    source_external_id: str,
    documents: Sequence[LangChainDocumentLike],
    index_external_id: str,
    index_revision_external_id: str,
    sequence: int,
    previous_documents: Sequence[LangChainDocumentLike] = (),
    require_physical_readback: bool = True,
) -> RemoteLangChainIndexWrite:
    """Write PGVector first, publish exact evidence through SDE without DB access.

    Strict physical readback is the default: legacy ID-ACK-only mode requires
    an explicit require_physical_readback=False opt-out. Chunk materializations
    precede index materialization. If the PGVector ACK or readback is
    incomplete no observation is published. On HTTP failure, publications may
    be partial: reconcile physical content and replay the identical immutable
    observations before starting another revision. No implicit retries.
    """
    if type(require_physical_readback) is not bool:
        raise TypeError("require_physical_readback must be a boolean.")
    if require_physical_readback:
        ensure_physical_readback_available(vector_store)
    if not isinstance(source_external_id, str) or not source_external_id.strip():
        raise ValueError("source_external_id is required.")
    prepared = prepare_langchain_documents_for_indexing(
        documents=documents, previous_documents=previous_documents
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
    expected_ids = tuple(chunk.asset_external_id for chunk in prepared.provenance)
    acknowledged = vector_store.add_documents(
        list(prepared.documents), ids=list(expected_ids)
    )
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

    # Publish the terminal index observation only after every chunk ACK and
    # corresponding SDE publication succeeded. Do not authorize execution.
    publications = tuple(
        lineage_client.publish_lineage_observation(observation)
        for observation in materialization.observations
    )
    return RemoteLangChainIndexWrite(
        document_ids=expected_ids,
        materialization=materialization,
        publications=publications,
        point_in_time_readback_verified=expected_readback is not None,
    )
