from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.integrations.integrity_client import (
    ContextIntegrityAssessment,
    ContextIntegritySummary,
    IntegrityRevision,
    SDEIntegrityTransportError,
    TargetIntegrityAssessment,
)
from src.integrations.langchain.context_integrity import (
    integrity_revisions_from_langchain_context,
    query_langchain_context_integrity,
)
from src.integrations.langchain.lineage import (
    CHUNK_REVISION_EXTERNAL_ID_KEY,
    SOURCE_EXTERNAL_ID_KEY,
)


@dataclass
class _Document:
    id: str | None
    metadata: dict[str, object]
    page_content: str


def _document(
    *,
    source: str,
    asset: str,
    revision: str,
) -> _Document:
    return _Document(
        id=asset,
        metadata={
            SOURCE_EXTERNAL_ID_KEY: source,
            CHUNK_REVISION_EXTERNAL_ID_KEY: revision,
        },
        page_content="content",
    )


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[IntegrityRevision, ...]] = []

    def query_context_integrity(
        self,
        targets: tuple[IntegrityRevision, ...],
    ) -> ContextIntegrityAssessment:
        self.calls.append(targets)
        return ContextIntegrityAssessment(
            assessments=tuple(
                TargetIntegrityAssessment(
                    target=target,
                    status="not_affected",
                    evidence=(),
                )
                for target in targets
            ),
            summary=ContextIntegritySummary(
                target_count=len(targets),
                unknown_count=0,
                not_affected_count=len(targets),
                affected_count=0,
                verified_count=0,
                has_unknown=False,
                has_unresolved_impact=False,
            ),
        )


class _FailingClient:
    def query_context_integrity(
        self,
        _targets: tuple[IntegrityRevision, ...],
    ) -> ContextIntegrityAssessment:
        raise SDEIntegrityTransportError("unavailable")


def test_context_conversion_preserves_order_and_exact_identity() -> None:
    first = _document(source="source-a", asset="chunk-a", revision="chunk-a-r2")
    second = _document(source="source-b", asset="chunk-b", revision="chunk-b-r4")

    revisions = integrity_revisions_from_langchain_context(
        retrieved_documents=(first, second, first),
    )

    assert revisions == (
        IntegrityRevision("source-a", "chunk-a", "chunk-a-r2"),
        IntegrityRevision("source-b", "chunk-b", "chunk-b-r4"),
    )


def test_query_composes_manifest_with_runtime_client_once() -> None:
    client = _RecordingClient()
    documents = (
        _document(source="source-a", asset="chunk-a", revision="chunk-a-r2"),
        _document(source="source-b", asset="chunk-b", revision="chunk-b-r4"),
    )

    serving_revision = IntegrityRevision(
        "source-a",
        "support-index",
        "support-index-r7",
    )

    result = query_langchain_context_integrity(
        client=client,
        serving_revision=serving_revision,
        retrieved_documents=documents,
    )

    assert len(client.calls) == 1
    assert client.calls[0] == (
        serving_revision,
        IntegrityRevision("source-a", "chunk-a", "chunk-a-r2"),
        IntegrityRevision("source-b", "chunk-b", "chunk-b-r4"),
    )
    assert result.summary.target_count == 3


def test_query_does_not_translate_transport_failure_into_integrity_state() -> None:
    document = _document(
        source="source-a",
        asset="chunk-a",
        revision="chunk-a-r2",
    )

    with pytest.raises(SDEIntegrityTransportError, match="unavailable"):
        query_langchain_context_integrity(
            client=_FailingClient(),
            serving_revision=IntegrityRevision(
                "source-a",
                "support-index",
                "support-index-r7",
            ),
            retrieved_documents=(document,),
        )


def test_query_deduplicates_serving_revision_from_retrieved_context() -> None:
    client = _RecordingClient()
    serving_revision = IntegrityRevision(
        "source-a",
        "support-index",
        "support-index-r7",
    )
    document = _document(
        source="source-a",
        asset="support-index",
        revision="support-index-r7",
    )

    query_langchain_context_integrity(
        client=client,
        serving_revision=serving_revision,
        retrieved_documents=(document,),
    )

    assert client.calls == [(serving_revision,)]
