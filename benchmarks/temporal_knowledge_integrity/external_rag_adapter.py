from __future__ import annotations

import hashlib
from dataclasses import dataclass

from benchmarks.temporal_knowledge_integrity.external_rag_stack import (
    ExternalAgentBinding,
    ExternalIndexWrite,
    ExternalVectorWrite,
)
from benchmarks.temporal_knowledge_integrity.materialization_event import (
    MaterializationEvent,
    RevisionReference,
)


@dataclass(frozen=True)
class CapturedMaterialization:
    events: tuple[MaterializationEvent, ...]


class ExternalRagLineageAdapter:
    def __init__(
        self,
        *,
        source_external_id: str,
        snapshot_external_id: str,
        sequence: int,
    ) -> None:
        self._source_external_id = source_external_id
        self._snapshot_external_id = snapshot_external_id
        self._sequence = sequence
        self._revision_by_asset_id: dict[str, str] = {}
        self._observed_revisions_by_asset_id: dict[
            str,
            set[str],
        ] = {}
        self._events: list[MaterializationEvent] = []

    def capture_index_write(
        self,
        write: ExternalIndexWrite,
    ) -> None:
        chunk_references: list[RevisionReference] = []

        for chunk in write.chunks:
            chunk_revision = _revision_id(
                asset_external_id=chunk.chunk_id,
                materialized_value=chunk.text,
            )
            self._revision_by_asset_id[
                chunk.chunk_id
            ] = chunk_revision

            self._observed_revisions_by_asset_id.setdefault(
                chunk.chunk_id,
                set(),
            ).add(
                chunk_revision
            )

            self._events.append(
                MaterializationEvent(
                    source_external_id=(
                        self._source_external_id
                    ),
                    snapshot_external_id=(
                        self._snapshot_external_id
                    ),
                    asset_external_id=chunk.chunk_id,
                    asset_kind="chunk",
                    revision_external_id=chunk_revision,
                    sequence=self._sequence,
                )
            )

            chunk_references.append(
                RevisionReference(
                    asset_external_id=chunk.chunk_id,
                    revision_external_id=chunk_revision,
                    dependency_type="indexed_from",
                )
            )

        index_revision = _revision_id(
            asset_external_id=write.index_id,
            materialized_value="|".join(
                reference.revision_external_id
                for reference in chunk_references
            ),
        )
        self._revision_by_asset_id[
            write.index_id
        ] = index_revision

        self._observed_revisions_by_asset_id.setdefault(
            write.index_id,
            set(),
        ).add(
            index_revision
        )

        self._events.append(
            MaterializationEvent(
                source_external_id=(
                    self._source_external_id
                ),
                snapshot_external_id=(
                    self._snapshot_external_id
                ),
                asset_external_id=write.index_id,
                asset_kind="index",
                revision_external_id=index_revision,
                sequence=self._sequence,
                dependencies=tuple(chunk_references),
            )
        )
    def capture_vector_write(
        self,
        write: ExternalVectorWrite,
    ) -> None:
        raise ValueError(
            "Exact chunk revision provenance is unavailable "
            "at the vector-write boundary."
        )
    def capture_agent_binding(
        self,
        binding: ExternalAgentBinding,
    ) -> None:
        index_revision = (
            binding.index_revision_external_id
        )

        if index_revision is None:
            raise ValueError(
                "Agent binding must provide the exact "
                "consumed index revision."
            )

        observed_index_revisions = (
            self._observed_revisions_by_asset_id.get(
                binding.index_id
            )
        )

        if not observed_index_revisions:
            raise ValueError(
                "Agent binding references an index "
                "that was not observed by the adapter."
            )

        if index_revision not in observed_index_revisions:
            raise ValueError(
                "Agent binding references an index revision "
                "that was not observed by the adapter."
            )

        agent_revision = _revision_id(
            asset_external_id=binding.agent_id,
            materialized_value=index_revision,
        )

        self._events.append(
            MaterializationEvent(
                source_external_id=(
                    self._source_external_id
                ),
                snapshot_external_id=(
                    self._snapshot_external_id
                ),
                asset_external_id=binding.agent_id,
                asset_kind="agent",
                revision_external_id=agent_revision,
                sequence=self._sequence,
                critical=binding.critical,
                dependencies=(
                    RevisionReference(
                        asset_external_id=binding.index_id,
                        revision_external_id=index_revision,
                        dependency_type="retrieves_from",
                    ),
                ),
            )
        )

    def captured(self) -> CapturedMaterialization:
        return CapturedMaterialization(
            events=tuple(self._events)
        )


def _revision_id(
    *,
    asset_external_id: str,
    materialized_value: str,
) -> str:
    digest = hashlib.sha256(
        materialized_value.encode("utf-8")
    ).hexdigest()[:16]

    return f"{asset_external_id}:{digest}"
