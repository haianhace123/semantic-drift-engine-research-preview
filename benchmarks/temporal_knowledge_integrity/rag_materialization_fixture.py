from __future__ import annotations

import hashlib
from dataclasses import dataclass

from benchmarks.temporal_knowledge_integrity.materialization_event import (
    MaterializationEvent,
    RevisionReference,
)

@dataclass(frozen=True)
class RagChunkInput:
    text: str
    stable_external_id: str | None = None

@dataclass(frozen=True)
class RagChunk:
    asset_external_id: str
    revision_external_id: str
    text: str


@dataclass(frozen=True)
class RagIndex:
    asset_external_id: str
    revision_external_id: str
    chunk_revision_ids: tuple[str, ...]


@dataclass(frozen=True)
class RagAgent:
    asset_external_id: str
    revision_external_id: str
    index_revision_external_id: str


def materialize_chunks(
    *,
    source_external_id: str,
    snapshot_external_id: str,
    text: str,
    chunk_size: int,
    sequence: int,
    previous_chunks: tuple[RagChunk, ...] = (),
    chunk_inputs: tuple[RagChunkInput, ...] | None = None,
) -> tuple[
    tuple[RagChunk, ...],
    tuple[MaterializationEvent, ...],
]:
    if chunk_size <= 0:
        raise ValueError(
            "chunk_size must be greater than zero."
        )

    normalized_source_external_id = (
        _normalize_identifier(
            source_external_id,
            field_name="source_external_id",
        )
    )
    normalized_snapshot_external_id = (
        _normalize_identifier(
            snapshot_external_id,
            field_name="snapshot_external_id",
        )
    )

    if chunk_inputs is None:
        resolved_chunk_inputs = tuple(
            RagChunkInput(
                text=text[start:start + chunk_size],
            )
            for start in range(
                0,
                len(text),
                chunk_size,
            )
            if text[start:start + chunk_size]
        )
    else:
        resolved_chunk_inputs = chunk_inputs

        if any(
            not chunk_input.text
            for chunk_input in resolved_chunk_inputs
        ):
            raise ValueError(
                "Chunk input text must not be empty."
            )
    explicit_asset_ids = [
        _normalize_identifier(
            chunk_input.stable_external_id,
            field_name="stable_external_id",
        )
        for chunk_input in resolved_chunk_inputs
        if chunk_input.stable_external_id is not None
    ]

    if len(explicit_asset_ids) != len(
        set(explicit_asset_ids)
    ):
        raise ValueError(
            "Duplicate chunk asset identity."
        )
    previous_asset_ids_by_text: dict[
        str,
        list[str],
    ] = {}

    for previous_chunk in previous_chunks:
        previous_asset_ids_by_text.setdefault(
            previous_chunk.text,
            [],
        ).append(
            previous_chunk.asset_external_id
        )

    next_asset_ordinal = _next_chunk_asset_ordinal(
        source_external_id=normalized_source_external_id,
        previous_chunks=previous_chunks,
    )

    chunks: list[RagChunk] = []
    events: list[MaterializationEvent] = []

    for chunk_input in resolved_chunk_inputs:
        chunk_text = chunk_input.text

        if chunk_input.stable_external_id is not None:
            asset_external_id = _normalize_identifier(
                chunk_input.stable_external_id,
                field_name="stable_external_id",
            )
        else:
            previous_asset_ids = (
                previous_asset_ids_by_text.get(
                    chunk_text,
                    [],
                )
            )

            if previous_asset_ids:
                asset_external_id = (
                    previous_asset_ids.pop(0)
                )
            else:
                asset_external_id = (
                    f"{normalized_source_external_id}:"
                    f"chunk:{next_asset_ordinal}"
                )
                next_asset_ordinal += 1

        revision_external_id = (
            f"{asset_external_id}:"
            f"{_content_digest(chunk_text)}"
        )

        chunk = RagChunk(
            asset_external_id=asset_external_id,
            revision_external_id=revision_external_id,
            text=chunk_text,
        )
        chunks.append(chunk)

        events.append(
            MaterializationEvent(
                source_external_id=(
                    normalized_source_external_id
                ),
                snapshot_external_id=(
                    normalized_snapshot_external_id
                ),
                asset_external_id=asset_external_id,
                asset_kind="chunk",
                revision_external_id=(
                    revision_external_id
                ),
                sequence=sequence,
            )
        )

    return tuple(chunks), tuple(events)


def materialize_index(
    *,
    source_external_id: str,
    snapshot_external_id: str,
    index_external_id: str,
    chunks: tuple[RagChunk, ...],
    sequence: int,
) -> tuple[RagIndex, MaterializationEvent]:
    normalized_source_external_id = (
        _normalize_identifier(
            source_external_id,
            field_name="source_external_id",
        )
    )
    normalized_snapshot_external_id = (
        _normalize_identifier(
            snapshot_external_id,
            field_name="snapshot_external_id",
        )
    )
    normalized_index_external_id = (
        _normalize_identifier(
            index_external_id,
            field_name="index_external_id",
        )
    )

    if not chunks:
        raise ValueError(
            "Index materialization requires at least one chunk."
        )

    chunk_revision_ids = tuple(
        chunk.revision_external_id
        for chunk in chunks
    )

    revision_external_id = (
        f"{normalized_index_external_id}:"
        f"{_content_digest('|'.join(chunk_revision_ids))}"
    )

    index = RagIndex(
        asset_external_id=normalized_index_external_id,
        revision_external_id=revision_external_id,
        chunk_revision_ids=chunk_revision_ids,
    )

    event = MaterializationEvent(
        source_external_id=normalized_source_external_id,
        snapshot_external_id=(
            normalized_snapshot_external_id
        ),
        asset_external_id=normalized_index_external_id,
        asset_kind="index",
        revision_external_id=revision_external_id,
        sequence=sequence,
        dependencies=tuple(
            RevisionReference(
                asset_external_id=(
                    chunk.asset_external_id
                ),
                revision_external_id=(
                    chunk.revision_external_id
                ),
                dependency_type="indexed_from",
            )
            for chunk in chunks
        ),
    )

    return index, event


def materialize_agent(
    *,
    source_external_id: str,
    snapshot_external_id: str,
    agent_external_id: str,
    index: RagIndex,
    sequence: int,
    critical: bool = True,
) -> tuple[RagAgent, MaterializationEvent]:
    normalized_source_external_id = (
        _normalize_identifier(
            source_external_id,
            field_name="source_external_id",
        )
    )
    normalized_snapshot_external_id = (
        _normalize_identifier(
            snapshot_external_id,
            field_name="snapshot_external_id",
        )
    )
    normalized_agent_external_id = (
        _normalize_identifier(
            agent_external_id,
            field_name="agent_external_id",
        )
    )

    revision_external_id = (
        f"{normalized_agent_external_id}:"
        f"{_content_digest(index.revision_external_id)}"
    )

    agent = RagAgent(
        asset_external_id=normalized_agent_external_id,
        revision_external_id=revision_external_id,
        index_revision_external_id=(
            index.revision_external_id
        ),
    )

    event = MaterializationEvent(
        source_external_id=normalized_source_external_id,
        snapshot_external_id=(
            normalized_snapshot_external_id
        ),
        asset_external_id=normalized_agent_external_id,
        asset_kind="agent",
        revision_external_id=revision_external_id,
        sequence=sequence,
        critical=critical,
        dependencies=(
            RevisionReference(
                asset_external_id=(
                    index.asset_external_id
                ),
                revision_external_id=(
                    index.revision_external_id
                ),
                dependency_type="retrieves_from",
            ),
        ),
    )

    return agent, event

def _next_chunk_asset_ordinal(
    *,
    source_external_id: str,
    previous_chunks: tuple[RagChunk, ...],
) -> int:
    prefix = f"{source_external_id}:chunk:"
    ordinals: list[int] = []

    for chunk in previous_chunks:
        if not chunk.asset_external_id.startswith(
            prefix
        ):
            continue

        suffix = chunk.asset_external_id[
            len(prefix):
        ]

        if suffix.isdigit():
            ordinals.append(int(suffix))

    if not ordinals:
        return 0

    return max(ordinals) + 1

def _content_digest(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()[:16]


def _normalize_identifier(
    value: str,
    *,
    field_name: str,
) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError(
            f"{field_name} must not be empty."
        )

    return normalized
