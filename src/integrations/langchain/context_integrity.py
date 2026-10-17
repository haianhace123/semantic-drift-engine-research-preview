from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from src.integrations.integrity_client import (
    ContextIntegrityAssessment,
    IntegrityRevision,
    validate_context_integrity_assessment,
)
from src.integrations.langchain.lineage import (
    LangChainDocumentLike,
    capture_langchain_context_revisions,
)


class ContextIntegrityClientLike(Protocol):
    def query_context_integrity(
        self,
        targets: Sequence[IntegrityRevision],
    ) -> ContextIntegrityAssessment: ...


def integrity_revisions_from_langchain_context(
    *,
    retrieved_documents: Sequence[LangChainDocumentLike],
) -> tuple[IntegrityRevision, ...]:
    """Build the exact SDE runtime manifest for one retrieved context."""

    return tuple(
        IntegrityRevision(
            source_external_id=revision.source_external_id,
            asset_external_id=revision.asset_external_id,
            revision_external_id=revision.revision_external_id,
        )
        for revision in capture_langchain_context_revisions(
            retrieved_documents=retrieved_documents,
        )
    )


def query_langchain_context_integrity(
    *,
    client: ContextIntegrityClientLike,
    serving_revision: IntegrityRevision,
    retrieved_documents: Sequence[LangChainDocumentLike],
) -> ContextIntegrityAssessment:
    """Query SDE for the serving revision and exact retrieved revisions."""

    retrieved_revisions = integrity_revisions_from_langchain_context(
        retrieved_documents=retrieved_documents,
    )
    targets = (
        serving_revision,
        *(
            revision
            for revision in retrieved_revisions
            if revision != serving_revision
        ),
    )
    assessment = client.query_context_integrity(targets)
    validate_context_integrity_assessment(
        assessment, requested_targets=targets,
    )
    return assessment
