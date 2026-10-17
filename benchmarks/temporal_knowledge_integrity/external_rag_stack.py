from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExternalChunk:
    chunk_id: str
    text: str


@dataclass(frozen=True)
class ExternalIndexWrite:
    index_id: str
    chunks: tuple[ExternalChunk, ...]

@dataclass(frozen=True)
class ExternalVectorRecord:
    vector_id: str
    embedding: tuple[float, ...]


@dataclass(frozen=True)
class ExternalVectorWrite:
    index_id: str
    records: tuple[ExternalVectorRecord, ...]

@dataclass(frozen=True)
class ExternalAgentBinding:
    agent_id: str
    index_id: str
    index_revision_external_id: str | None = None
    critical: bool = False


class ExternalRagStack:
    def __init__(self) -> None:
        self._index_writes: list[ExternalIndexWrite] = []
        self._vector_writes: list[ExternalVectorWrite] = []
        self._agent_bindings: list[ExternalAgentBinding] = []
    @property
    def vector_writes(self) -> tuple[ExternalVectorWrite, ...]:
        return tuple(self._vector_writes)

    @property
    def index_writes(self) -> tuple[ExternalIndexWrite, ...]:
        return tuple(self._index_writes)

    @property
    def agent_bindings(self) -> tuple[ExternalAgentBinding, ...]:
        return tuple(self._agent_bindings)

    def upsert_index(
        self,
        *,
        index_id: str,
        chunks: tuple[ExternalChunk, ...],
    ) -> None:
        self._index_writes.append(
            ExternalIndexWrite(
                index_id=index_id,
                chunks=chunks,
            )
        )

    def bind_agent(
        self,
        *,
        agent_id: str,
        index_id: str,
        index_revision_external_id: str | None = None,
        critical: bool = False,
    ) -> None:
        self._agent_bindings.append(
            ExternalAgentBinding(
                agent_id=agent_id,
                index_id=index_id,
                index_revision_external_id=(
                    index_revision_external_id
                ),
                critical=critical,
            )
        )
    def upsert_vectors(
        self,
        *,
        index_id: str,
        records: tuple[ExternalVectorRecord, ...],
    ) -> None:
        self._vector_writes.append(
            ExternalVectorWrite(
                index_id=index_id,
                records=records,
            )
        )
