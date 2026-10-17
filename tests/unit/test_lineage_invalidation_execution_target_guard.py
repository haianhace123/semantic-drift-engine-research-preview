from __future__ import annotations

import uuid

import pytest

from src.domain.lineage_invalidation import (
    LineageInvalidationDecision,
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_revision import LineageRevisionIdentity
from src.services.lineage_invalidation_execution_target_guard import (
    LineageInvalidationExecutionTargetCausalMismatchError,
    resolve_causal_chunk_target_configuration,
)


def _decision():
    root = LineageRevisionIdentity("source-a", "chunk-a", "chunk-a-r3")
    target = LineageRevisionIdentity("source-a", "index-a", "index-a-r7")
    return LineageInvalidationDecision(
        root=root,
        target=target,
        policy=LineageInvalidationPolicyIdentity("rag-invalidation", 7),
        disposition="invalidate",
        reason_code="causal_dependency_changed",
        causal_path=(root, target),
    )


@pytest.mark.parametrize(
    "config",
    [
        {"connection_ref": "downstream", "collection_name": "index", "document_id": "chunk-a"},
        {"connection_ref": "downstream", "collection_name": "index", "document_revision_external_id": "chunk-a-r3"},
        {"connection_ref": "downstream", "collection_name": "index", "source_external_id": "source-a"},
        {"connection_ref": "downstream"},
        {"collection_name": "index"},
        {"connection_ref": "  ", "collection_name": "index"},
    ],
)
def test_causal_chunk_binding_rejects_physical_identity_or_missing_keys(config):
    # Rejection must happen before querying lineage or issuing downstream SQL.
    with pytest.raises(LineageInvalidationExecutionTargetCausalMismatchError):
        resolve_causal_chunk_target_configuration(
            db=None,
            organization_id=uuid.uuid4(),
            decision=_decision(),
            provider="pgvector",
            target_kind="langchain_pgvector_causal_chunk",
            configuration=config,
        )


@pytest.mark.parametrize(
    ("provider", "kind"),
    [
        ("pgvector", "langchain_pgvector_document_revision"),
        ("pgvector", "langchain_pgvector_document"),
        ("other", "langchain_pgvector_causal_chunk"),
    ],
)
def test_causal_chunk_resolver_rejects_legacy_or_unknown_target(provider, kind):
    with pytest.raises(LineageInvalidationExecutionTargetCausalMismatchError):
        resolve_causal_chunk_target_configuration(
            db=None,
            organization_id=uuid.uuid4(),
            decision=_decision(),
            provider=provider,
            target_kind=kind,
            configuration={
                "connection_ref": "downstream",
                "collection_name": "index",
            },
        )
