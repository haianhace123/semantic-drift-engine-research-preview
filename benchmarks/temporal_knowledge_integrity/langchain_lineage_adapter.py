from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from benchmarks.temporal_knowledge_integrity.materialization_event import (
    MaterializationEvent,
    RevisionReference,
)
from src.domain.lineage_observation import LineageObservation
from src.integrations.langchain import (
    LangChainChunkProvenance,
    LangChainConsumerAssetKind,
    LangChainDocumentLike,
    acquire_langchain_chunk_provenance,
)
from src.integrations.langchain import (
    capture_langchain_consumer_binding as capture_supported_consumer_binding,
)
from src.integrations.langchain import (
    capture_langchain_index_materialization as capture_supported_index_materialization,
)


@dataclass(frozen=True)
class LangChainIndexMaterialization:
    index_external_id: str
    index_revision_external_id: str
    events: tuple[MaterializationEvent, ...]


@dataclass(frozen=True)
class LangChainConsumerBinding:
    consumer_external_id: str
    consumer_revision_external_id: str
    event: MaterializationEvent


def capture_langchain_consumer_binding(
    *,
    retrieved_documents: Sequence[LangChainDocumentLike],
    consumer_external_id: str,
    consumer_revision_external_id: str,
    consumer_asset_kind: LangChainConsumerAssetKind,
    binding_snapshot_external_id: str,
    sequence: int,
) -> LangChainConsumerBinding:
    captured = capture_supported_consumer_binding(
        retrieved_documents=retrieved_documents,
        consumer_external_id=consumer_external_id,
        consumer_revision_external_id=consumer_revision_external_id,
        consumer_asset_kind=consumer_asset_kind,
        binding_snapshot_external_id=binding_snapshot_external_id,
        sequence=sequence,
    )

    return LangChainConsumerBinding(
        consumer_external_id=captured.consumer_external_id,
        consumer_revision_external_id=(
            captured.consumer_revision_external_id
        ),
        event=_to_materialization_event(captured.observation),
    )


def capture_langchain_index_materialization(
    *,
    documents: Sequence[LangChainDocumentLike],
    index_external_id: str,
    index_revision_external_id: str,
    sequence: int,
) -> LangChainIndexMaterialization:
    captured = capture_supported_index_materialization(
        documents=documents,
        index_external_id=index_external_id,
        index_revision_external_id=index_revision_external_id,
        sequence=sequence,
    )

    return LangChainIndexMaterialization(
        index_external_id=captured.index_external_id,
        index_revision_external_id=(
            captured.index_revision_external_id
        ),
        events=tuple(
            _to_materialization_event(observation)
            for observation in captured.observations
        ),
    )


def _to_materialization_event(
    observation: LineageObservation,
) -> MaterializationEvent:
    return MaterializationEvent(
        source_external_id=observation.source_external_id,
        snapshot_external_id=observation.snapshot_external_id,
        asset_external_id=observation.asset_external_id,
        asset_kind=observation.asset_kind,
        revision_external_id=observation.revision_external_id,
        sequence=observation.sequence,
        dependencies=tuple(
            RevisionReference(
                asset_external_id=dependency.asset_external_id,
                revision_external_id=dependency.revision_external_id,
                dependency_type=dependency.dependency_type,
            )
            for dependency in observation.dependencies
        ),
    )