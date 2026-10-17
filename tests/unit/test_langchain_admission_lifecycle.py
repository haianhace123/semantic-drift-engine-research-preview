"""Request-time admission contract across a changing knowledge lifecycle.

These deterministic contract cases do not replace live SDE decision tests.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.integrations.integrity_client import (
    ContextIntegrityAssessment,
    ContextIntegritySummary,
    IntegrityRevision,
    TargetIntegrityAssessment,
    SDEIntegrityTransportError,
)
from src.integrations.langchain import (
    LangChainContextNotAdmittedError,
    admit_langchain_context,
)


@dataclass
class Document:
    id: str
    page_content: str
    metadata: dict[str, str]


SERVING = IntegrityRevision("policy-source", "support-index", "support-index@v1")
CHUNK_V1 = Document(
    id="refund-chunk",
    page_content="Refunds accepted for 30 days.",
    metadata={
        "source_external_id": "policy-source",
        "chunk_revision_external_id": "refund-chunk@v1",
    },
)
CHUNK_V2 = Document(
    id="refund-chunk",
    page_content="Refunds accepted for 14 days.",
    metadata={
        "source_external_id": "policy-source",
        "chunk_revision_external_id": "refund-chunk@v2",
    },
)


class SequencedIntegrityService:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requested = []

    def query_context_integrity(self, targets):
        self.requested.append(tuple(targets))
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert len(targets) == len(outcome)
        counts = {name: outcome.count(name) for name in (
            "unknown", "not_affected", "affected", "verified"
        )}
        return ContextIntegrityAssessment(
            assessments=tuple(
                TargetIntegrityAssessment(target=target, status=status, evidence=())
                for target, status in zip(targets, outcome, strict=True)
            ),
            summary=ContextIntegritySummary(
                target_count=len(targets),
                unknown_count=counts["unknown"],
                not_affected_count=counts["not_affected"],
                affected_count=counts["affected"],
                verified_count=counts["verified"],
                has_unknown=counts["unknown"] > 0,
                has_unresolved_impact=counts["affected"] > 0,
            ),
        )


def _serve(service, document, actions):
    admitted = admit_langchain_context(
        client=service,
        serving_revision=SERVING,
        retrieved_documents=(document,),
    )
    # Represents the sole generation boundary used by this test consumer.
    actions.append(admitted.documents[0].page_content)
    return admitted


def test_unknown_known_unresolved_verified_lifecycle_rechecks_every_request():
    service = SequencedIntegrityService([
        ("unknown", "unknown"),
        ("not_affected", "not_affected"),
        ("affected", "not_affected"),
        ("verified", "not_affected"),
    ])
    actions = []
    with pytest.raises(LangChainContextNotAdmittedError, match="unknown_integrity"):
        _serve(service, CHUNK_V1, actions)
    assert actions == []

    _serve(service, CHUNK_V1, actions)
    assert actions == [CHUNK_V1.page_content]

    with pytest.raises(LangChainContextNotAdmittedError, match="unresolved_impact"):
        _serve(service, CHUNK_V1, actions)
    assert actions == [CHUNK_V1.page_content]

    # Verifying the replacement is not permission to serve the retired v1.
    with pytest.raises(LangChainContextNotAdmittedError, match="superseded_revision"):
        _serve(service, CHUNK_V1, actions)
    assert actions == [CHUNK_V1.page_content]
    assert len(service.requested) == 4
    assert all(request[0] == SERVING for request in service.requested)
    assert all(request[1].revision_external_id == "refund-chunk@v1" for request in service.requested)


def test_new_chunk_revision_requires_new_admission():
    service = SequencedIntegrityService([
        ("not_affected", "not_affected"),
        ("unknown", "unknown"),
        ("not_affected", "not_affected"),
    ])
    actions = []
    _serve(service, CHUNK_V1, actions)
    with pytest.raises(LangChainContextNotAdmittedError, match="unknown_integrity"):
        _serve(service, CHUNK_V2, actions)
    assert actions == [CHUNK_V1.page_content]
    _serve(service, CHUNK_V2, actions)
    assert actions == [CHUNK_V1.page_content, CHUNK_V2.page_content]
    assert service.requested[1][1].revision_external_id == "refund-chunk@v2"


def test_transport_outage_does_not_reuse_prior_admission():
    service = SequencedIntegrityService([
        ("not_affected", "not_affected"),
        SDEIntegrityTransportError("service unavailable"),
    ])
    actions = []
    _serve(service, CHUNK_V1, actions)
    with pytest.raises(SDEIntegrityTransportError):
        _serve(service, CHUNK_V1, actions)
    assert actions == [CHUNK_V1.page_content]
    assert len(service.requested) == 2
