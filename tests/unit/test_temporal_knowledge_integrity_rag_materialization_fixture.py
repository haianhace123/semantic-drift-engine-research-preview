from benchmarks.temporal_knowledge_integrity.materialization_event import (
    build_lineage_from_projection,
    project_materialization_events,
)
from benchmarks.temporal_knowledge_integrity.rag_materialization_fixture import (
    materialize_agent,
    materialize_chunks,
    materialize_index,
    RagChunkInput,
)
import pytest


def test_external_rag_materialization_projects_complete_lineage() -> None:
    chunks, chunk_events = materialize_chunks(
        source_external_id="policy",
        snapshot_external_id="snapshot-v1",
        text="AAAABBBB",
        chunk_size=4,
        sequence=1,
    )
    index, index_event = materialize_index(
        source_external_id="policy",
        snapshot_external_id="snapshot-v1",
        index_external_id="refund-index",
        chunks=chunks,
        sequence=1,
    )
    _, agent_event = materialize_agent(
        source_external_id="policy",
        snapshot_external_id="snapshot-v1",
        agent_external_id="support-agent",
        index=index,
        sequence=1,
    )

    projection = project_materialization_events(
        (
            *chunk_events,
            index_event,
            agent_event,
        )
    )
    observation = build_lineage_from_projection(
        projection,
        content_hash="a" * 64,
    )

    logical_edges = {
        (
            dependency.upstream_external_id,
            dependency.downstream_external_id,
            dependency.dependency_type,
        )
        for dependency in observation.dependencies
    }

    assert (
        "policy:chunk:0",
        "refund-index",
        "indexed_from",
    ) in logical_edges
    assert (
        "policy:chunk:1",
        "refund-index",
        "indexed_from",
    ) in logical_edges
    assert (
        "refund-index",
        "support-agent",
        "retrieves_from",
    ) in logical_edges

    revision_edges = {
        (
            dependency.upstream_asset_external_id,
            dependency.upstream_revision_external_id,
            dependency.downstream_asset_external_id,
            dependency.downstream_revision_external_id,
        )
        for dependency in observation.revision_dependencies
    }

    assert (
        chunks[0].asset_external_id,
        chunks[0].revision_external_id,
        index.asset_external_id,
        index.revision_external_id,
    ) in revision_edges


def test_unchanged_chunk_keeps_asset_and_revision_identity() -> None:
    previous_chunks, _ = materialize_chunks(
        source_external_id="policy",
        snapshot_external_id="snapshot-v1",
        text="AAAABBBB",
        chunk_size=4,
        sequence=1,
    )
    current_chunks, _ = materialize_chunks(
        source_external_id="policy",
        snapshot_external_id="snapshot-v2",
        text="AAAACCCC",
        chunk_size=4,
        sequence=2,
    )

    assert (
        previous_chunks[0].asset_external_id
        == current_chunks[0].asset_external_id
    )
    assert (
        previous_chunks[0].revision_external_id
        == current_chunks[0].revision_external_id
    )

    assert (
        previous_chunks[1].asset_external_id
        == current_chunks[1].asset_external_id
    )
    assert (
        previous_chunks[1].revision_external_id
        != current_chunks[1].revision_external_id
    )


def test_prefix_insertion_preserves_unchanged_chunk_identity() -> None:
    previous_chunks, _ = materialize_chunks(
        source_external_id="policy",
        snapshot_external_id="snapshot-v1",
        text="AAAABBBB",
        chunk_size=4,
        sequence=1,
    )
    current_chunks, _ = materialize_chunks(
        source_external_id="policy",
        snapshot_external_id="snapshot-v2",
        text="XXXXAAAABBBB",
        chunk_size=4,
        sequence=2,
        previous_chunks=previous_chunks,
    )

    previous_aaaa = next(
        chunk
        for chunk in previous_chunks
        if chunk.text == "AAAA"
    )
    current_aaaa = next(
        chunk
        for chunk in current_chunks
        if chunk.text == "AAAA"
    )

    assert (
        previous_aaaa.asset_external_id
        == current_aaaa.asset_external_id
    )

def test_duplicate_chunk_content_keeps_distinct_logical_assets() -> None:
    chunks, _ = materialize_chunks(
        source_external_id="policy",
        snapshot_external_id="snapshot-v1",
        text="AAAAAAAA",
        chunk_size=4,
        sequence=1,
    )

    assert len(chunks) == 2
    assert chunks[0].text == chunks[1].text
    assert (
        chunks[0].asset_external_id
        != chunks[1].asset_external_id
    )

def test_stable_external_identity_survives_content_mutation() -> None:
    previous_chunks, _ = materialize_chunks(
        source_external_id="policy",
        snapshot_external_id="snapshot-v1",
        text="",
        chunk_size=4,
        sequence=1,
        chunk_inputs=(
            RagChunkInput(
                text="AAAA",
                stable_external_id="policy:introduction",
            ),
            RagChunkInput(
                text="BBBB",
                stable_external_id="policy:refund-rule",
            ),
        ),
    )
    current_chunks, _ = materialize_chunks(
        source_external_id="policy",
        snapshot_external_id="snapshot-v2",
        text="",
        chunk_size=4,
        sequence=2,
        previous_chunks=previous_chunks,
        chunk_inputs=(
            RagChunkInput(
                text="AAAA",
                stable_external_id="policy:introduction",
            ),
            RagChunkInput(
                text="CCCC",
                stable_external_id="policy:refund-rule",
            ),
        ),
    )

    previous_changed_chunk = next(
        chunk
        for chunk in previous_chunks
        if chunk.asset_external_id
        == "policy:refund-rule"
    )
    current_changed_chunk = next(
        chunk
        for chunk in current_chunks
        if chunk.asset_external_id
        == "policy:refund-rule"
    )

    assert (
        previous_changed_chunk.asset_external_id
        == current_changed_chunk.asset_external_id
    )
    assert (
        previous_changed_chunk.revision_external_id
        != current_changed_chunk.revision_external_id
    )

def test_duplicate_stable_chunk_identity_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="Duplicate chunk asset identity",
    ):
        materialize_chunks(
            source_external_id="policy",
            snapshot_external_id="snapshot-v1",
            text="",
            chunk_size=4,
            sequence=1,
            chunk_inputs=(
                RagChunkInput(
                    text="AAAA",
                    stable_external_id="policy:refund-rule",
                ),
                RagChunkInput(
                    text="BBBB",
                    stable_external_id="policy:refund-rule",
                ),
            ),
        )
