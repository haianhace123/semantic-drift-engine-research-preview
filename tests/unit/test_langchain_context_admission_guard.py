from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from src.integrations.integrity_admission import IntegrityAdmissionPolicy
from src.integrations.integrity_client import (
    ContextIntegrityAssessment,
    ContextIntegritySummary,
    IntegrityRevision,
    SDEIntegrityTransportError,
    SDEIntegrityProtocolError,
    TargetIntegrityAssessment,
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


SERVING = IntegrityRevision("source-a", "index", "index-r1")
CHUNK = IntegrityRevision("source-a", "chunk", "chunk-r1")
DOC = Document(
    id="chunk",
    page_content="Sensitive user text should not appear in an admission error.",
    metadata={
        "source_external_id": "source-a",
        "chunk_revision_external_id": "chunk-r1",
    },
)


class Client:
    def __init__(self, statuses: tuple[str, ...], *, fail: bool = False):
        self.statuses = statuses
        self.fail = fail
        self.calls: list[tuple[IntegrityRevision, ...]] = []

    def query_context_integrity(self, targets):
        self.calls.append(tuple(targets))
        if self.fail:
            raise SDEIntegrityTransportError("SDE unavailable")
        assert tuple(targets) == (SERVING, CHUNK)
        counts = {status: self.statuses.count(status) for status in (
            "unknown", "affected", "verified", "not_affected"
        )}
        return ContextIntegrityAssessment(
            assessments=tuple(
                TargetIntegrityAssessment(target=target, status=status, evidence=())
                for target, status in zip(targets, self.statuses, strict=True)
            ),
            summary=ContextIntegritySummary(
                target_count=len(self.statuses),
                unknown_count=counts["unknown"],
                not_affected_count=counts["not_affected"],
                affected_count=counts["affected"],
                verified_count=counts["verified"],
                has_unknown=counts["unknown"] > 0,
                has_unresolved_impact=counts["affected"] > 0,
            ),
        )


def _admit(client, *, policy=None, documents=(DOC,)):
    return admit_langchain_context(
        client=client,
        serving_revision=SERVING,
        retrieved_documents=documents,
        policy=policy,
    )


def test_safe_context_returns_detached_immutable_documents_after_query():
    client = Client(("not_affected", "not_affected"))
    admitted = _admit(client)
    assert admitted.admission.outcome == "proceed"
    assert len(admitted.documents) == 1
    assert admitted.documents[0] is not DOC
    assert admitted.documents[0].id == DOC.id
    assert admitted.documents[0].page_content == DOC.page_content
    assert dict(admitted.documents[0].metadata) == DOC.metadata
    assert client.calls == [(SERVING, CHUNK)]


def test_repaired_old_chunk_is_still_blocked_before_generation():
    client = Client(("not_affected", "verified"))
    with pytest.raises(LangChainContextNotAdmittedError) as error:
        _admit(client)
    assert error.value.reason == "superseded_revision"
    assert "Sensitive" not in str(error.value)


def test_unknown_is_blocked_by_default_and_error_excludes_content():
    client = Client(("unknown", "not_affected"))
    with pytest.raises(LangChainContextNotAdmittedError) as error:
        _admit(client)
    assert error.value.reason == "unknown_integrity"
    assert "Sensitive" not in str(error.value)


def test_unresolved_impact_blocks_even_with_explicit_allow_unknown():
    client = Client(("affected", "unknown"))
    with pytest.raises(LangChainContextNotAdmittedError) as error:
        _admit(client, policy=IntegrityAdmissionPolicy(unknown="proceed"))
    assert error.value.reason == "unresolved_impact"


def test_unknown_can_proceed_only_when_caller_explicitly_opts_in():
    client = Client(("unknown", "unknown"))
    admitted = _admit(client, policy=IntegrityAdmissionPolicy(unknown="proceed"))
    assert admitted.admission.reason == "unknown_integrity"
    assert admitted.documents[0].page_content == DOC.page_content


def test_transport_failure_propagates_without_fallback():
    with pytest.raises(SDEIntegrityTransportError):
        _admit(Client(("unknown", "unknown"), fail=True))


def test_empty_context_is_not_admitted():
    client = Client(())
    with pytest.raises(ValueError, match="at least one retrieved document"):
        _admit(client, documents=())
    assert client.calls == []


def test_missing_provenance_is_not_admitted():
    bad_doc = Document(id="chunk", page_content="content", metadata={})
    client = Client(("not_affected", "not_affected"))
    with pytest.raises(ValueError, match="source_external_id"):
        _admit(client, documents=(bad_doc,))
    assert client.calls == []


def test_consumer_side_effects_only_after_admission():
    generated: list[str] = []

    def generate(admitted):
        generated.append(admitted.documents[0].page_content)

    with pytest.raises(LangChainContextNotAdmittedError):
        generate(_admit(Client(("affected", "unknown"))))
    assert generated == []
    generate(_admit(Client(("not_affected", "not_affected"))))
    assert generated == [DOC.page_content]


def test_custom_client_cannot_admit_a_clean_but_wrong_revision():
    class WrongRevisionClient(Client):
        def query_context_integrity(self, targets):
            result = super().query_context_integrity(targets)
            wrong = replace(
                result.assessments[1],
                target=IntegrityRevision("source-a", "chunk", "chunk-r999"),
            )
            return replace(
                result,
                assessments=(result.assessments[0], wrong),
            )

    with pytest.raises(SDEIntegrityProtocolError, match="requested revisions"):
        _admit(WrongRevisionClient(("not_affected", "not_affected")))


def test_custom_client_cannot_admit_an_incomplete_clean_response():
    class IncompleteClient(Client):
        def query_context_integrity(self, targets):
            result = super().query_context_integrity(targets)
            return replace(
                result,
                assessments=result.assessments[:1],
                summary=ContextIntegritySummary(
                    target_count=1,
                    unknown_count=0,
                    not_affected_count=1,
                    affected_count=0,
                    verified_count=0,
                    has_unknown=False,
                    has_unresolved_impact=False,
                ),
            )

    with pytest.raises(SDEIntegrityProtocolError, match="requested revisions"):
        _admit(IncompleteClient(("not_affected", "not_affected")))


def test_custom_client_cannot_admit_a_forged_clean_summary():
    class ForgedSummaryClient(Client):
        def query_context_integrity(self, targets):
            result = super().query_context_integrity(targets)
            return replace(
                result,
                summary=replace(result.summary, affected_count=0, has_unresolved_impact=False),
            )

    with pytest.raises(SDEIntegrityProtocolError, match="status counts"):
        _admit(ForgedSummaryClient(("not_affected", "affected")))
