from benchmarks.temporal_knowledge_integrity.external_rag_adapter import (
    ExternalRagLineageAdapter,
)
from benchmarks.temporal_knowledge_integrity.external_rag_stack import (
    ExternalChunk,
    ExternalRagStack,
    ExternalVectorRecord,
)
from benchmarks.temporal_knowledge_integrity.materialization_event import (
    build_lineage_from_projection,
    project_materialization_events,
)
import pytest

def test_adapter_captures_exact_external_rag_lineage() -> None:
    stack = ExternalRagStack()
    chunks = (
        ExternalChunk(
            chunk_id="policy:refund-rule",
            text="Refunds are allowed within 30 days.",
        ),
        ExternalChunk(
            chunk_id="policy:exceptions",
            text="Final-sale items are excluded.",
        ),
    )

    stack.upsert_index(
        index_id="support-index",
        chunks=chunks,
    )
    adapter = ExternalRagLineageAdapter(
        source_external_id="policy",
        snapshot_external_id="snapshot-v1",
        sequence=1,
    )

    adapter.capture_index_write(
        stack.index_writes[0]
    )

    index_event = next(
        event
        for event in adapter.captured().events
        if event.asset_external_id == "support-index"
    )

    stack.bind_agent(
        agent_id="support-agent",
        index_id="support-index",
        index_revision_external_id=(
            index_event.revision_external_id
        ),
        critical=True,
    )

    adapter.capture_agent_binding(
        stack.agent_bindings[0]
    )
    stack.bind_agent(
        agent_id="support-agent",
        index_id="support-index",
        critical=True,
    )

    adapter = ExternalRagLineageAdapter(
        source_external_id="policy",
        snapshot_external_id="snapshot-v1",
        sequence=1,
    )

    adapter.capture_index_write(
        stack.index_writes[0]
    )
    adapter.capture_agent_binding(
        stack.agent_bindings[0]
    )

    captured = adapter.captured()

    projection = project_materialization_events(
        captured.events,
        current_snapshot_external_id="snapshot-v1",
    )
    observation = build_lineage_from_projection(
        projection,
        content_hash="content-v1",
    )

    logical_dependencies = {
        (
            dependency.upstream_external_id,
            dependency.downstream_external_id,
            dependency.dependency_type,
        )
        for dependency in observation.dependencies
    }

    assert (
        "policy:refund-rule",
        "support-index",
        "indexed_from",
    ) in logical_dependencies
    assert (
        "policy:exceptions",
        "support-index",
        "indexed_from",
    ) in logical_dependencies
    assert (
        "support-index",
        "support-agent",
        "retrieves_from",
    ) in logical_dependencies


def test_adapter_records_exact_revision_dependencies() -> None:
    stack = ExternalRagStack()
    chunks = (
        ExternalChunk(
            chunk_id="policy:refund-rule",
            text="Refunds are allowed within 30 days.",
        ),
    )

    stack.upsert_index(
        index_id="support-index",
        chunks=chunks,
    )
    adapter = ExternalRagLineageAdapter(
        source_external_id="policy",
        snapshot_external_id="snapshot-v1",
        sequence=1,
    )
    adapter.capture_index_write(
        stack.index_writes[0]
    )

    index_event = next(
        event
        for event in adapter.captured().events
        if event.asset_external_id == "support-index"
    )

    stack.bind_agent(
        agent_id="support-agent",
        index_id="support-index",
        index_revision_external_id=(
            index_event.revision_external_id
        ),
    )

    adapter.capture_agent_binding(
        stack.agent_bindings[0]
    )

    projection = project_materialization_events(
        adapter.captured().events,
        current_snapshot_external_id="snapshot-v1",
    )
    observation = build_lineage_from_projection(
        projection,
        content_hash="content-v1",
    )

    revision_dependencies = {
        (
            dependency.upstream_asset_external_id,
            dependency.upstream_revision_external_id,
            dependency.downstream_asset_external_id,
            dependency.downstream_revision_external_id,
        )
        for dependency in observation.revision_dependencies
    }

    assert len(revision_dependencies) == 2

    chunk_to_index = next(
        dependency
        for dependency in observation.revision_dependencies
        if dependency.upstream_asset_external_id
        == "policy:refund-rule"
    )
    index_to_agent = next(
        dependency
        for dependency in observation.revision_dependencies
        if dependency.upstream_asset_external_id
        == "support-index"
    )

    assert (
        chunk_to_index.downstream_revision_external_id
        == index_to_agent.upstream_revision_external_id
    )

def test_lossy_vector_boundary_cannot_recover_exact_chunk_lineage() -> None:
    stack = ExternalRagStack()

    stack.upsert_vectors(
        index_id="support-index",
        records=(
            ExternalVectorRecord(
                vector_id="vector-001",
                embedding=(0.1, 0.2, 0.3),
            ),
            ExternalVectorRecord(
                vector_id="vector-002",
                embedding=(0.4, 0.5, 0.6),
            ),
        ),
    )

    adapter = ExternalRagLineageAdapter(
        source_external_id="policy",
        snapshot_external_id="snapshot-v1",
        sequence=1,
    )

    with pytest.raises(
        ValueError,
        match=(
            "Exact chunk revision provenance is unavailable "
            "at the vector-write boundary"
        ),
    ):
        adapter.capture_vector_write(
            stack.vector_writes[0]
        )

def test_agent_binding_does_not_assume_latest_observed_index_revision() -> None:
    adapter = ExternalRagLineageAdapter(
        source_external_id="policy",
        snapshot_external_id="snapshot-v2",
        sequence=2,
    )

    previous_stack = ExternalRagStack()
    previous_stack.upsert_index(
        index_id="support-index",
        chunks=(
            ExternalChunk(
                chunk_id="policy:refund-rule",
                text="Refunds are allowed within 30 days.",
            ),
        ),
    )

    current_stack = ExternalRagStack()
    current_stack.upsert_index(
        index_id="support-index",
        chunks=(
            ExternalChunk(
                chunk_id="policy:refund-rule",
                text="Refunds are allowed within 14 days.",
            ),
        ),
    )

    adapter.capture_index_write(
        previous_stack.index_writes[0]
    )
    previous_index_event = next(
        event
        for event in adapter.captured().events
        if event.asset_external_id == "support-index"
    )

    current_stack.bind_agent(
        agent_id="support-agent",
        index_id="support-index",
        index_revision_external_id=(
            previous_index_event.revision_external_id
        ),
        critical=True,
    )

    adapter.capture_agent_binding(
        current_stack.agent_bindings[0]
    )

    adapter.capture_index_write(
        current_stack.index_writes[0]
    )

    agent_event = next(
        event
        for event in reversed(
            adapter.captured().events
        )
        if event.asset_external_id == "support-agent"
    )

    assert (
        agent_event.dependencies[0].revision_external_id
        == previous_index_event.revision_external_id
    )

def test_agent_binding_without_exact_index_revision_is_rejected() -> None:
    stack = ExternalRagStack()
    stack.upsert_index(
        index_id="support-index",
        chunks=(
            ExternalChunk(
                chunk_id="policy:refund-rule",
                text="Refunds are allowed within 30 days.",
            ),
        ),
    )

    adapter = ExternalRagLineageAdapter(
        source_external_id="policy",
        snapshot_external_id="snapshot-v1",
        sequence=1,
    )
    adapter.capture_index_write(
        stack.index_writes[0]
    )

    stack.bind_agent(
        agent_id="support-agent",
        index_id="support-index",
    )

    with pytest.raises(
        ValueError,
        match=(
            "Agent binding must provide the exact "
            "consumed index revision"
        ),
    ):
        adapter.capture_agent_binding(
            stack.agent_bindings[0]
        )

def test_agent_binding_rejects_unobserved_index_revision() -> None:
    stack = ExternalRagStack()
    stack.upsert_index(
        index_id="support-index",
        chunks=(
            ExternalChunk(
                chunk_id="policy:refund-rule",
                text="Refunds are allowed within 30 days.",
            ),
        ),
    )

    adapter = ExternalRagLineageAdapter(
        source_external_id="policy",
        snapshot_external_id="snapshot-v1",
        sequence=1,
    )
    adapter.capture_index_write(
        stack.index_writes[0]
    )

    stack.bind_agent(
        agent_id="support-agent",
        index_id="support-index",
        index_revision_external_id=(
            "support-index:unobserved-revision"
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            "Agent binding references an index revision "
            "that was not observed by the adapter"
        ),
    ):
        adapter.capture_agent_binding(
            stack.agent_bindings[0]
        )
