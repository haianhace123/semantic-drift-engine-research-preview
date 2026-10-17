"""Guarded consumer tests; no live model, API, Docker, or database."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from experiments.guarded_langchain_consumer import run_guarded_model_call
from src.integrations.integrity_client import (
    ContextIntegrityAssessment,
    ContextIntegritySummary,
    IntegrityRevision,
    SDEIntegrityHTTPError,
    SDEIntegrityProtocolError,
    TargetIntegrityAssessment,
)
from src.integrations.langchain.context_admission import LangChainContextNotAdmittedError


@dataclass
class FakeDocument:
    id: str | None
    metadata: dict[str, Any]
    page_content: str


SERVING = IntegrityRevision("source-a", "index-1", "index-v1")


def document() -> FakeDocument:
    return FakeDocument(
        id="chunk-1",
        metadata={
            "source_external_id": "source-a",
            "chunk_revision_external_id": "chunk-1@v1",
        },
        page_content="Known approved context.",
    )


def assessment(targets, status):
    count = len(targets)
    counts = {label: count if label == status else 0 for label in (
        "unknown", "not_affected", "affected", "verified"
    )}
    return ContextIntegrityAssessment(
        assessments=tuple(
            TargetIntegrityAssessment(target=target, status=status, evidence=())
            for target in targets
        ),
        summary=ContextIntegritySummary(
            target_count=count,
            unknown_count=counts["unknown"],
            not_affected_count=counts["not_affected"],
            affected_count=counts["affected"],
            verified_count=counts["verified"],
            has_unknown=(status == "unknown"),
            has_unresolved_impact=(status == "affected"),
        ),
    )


class FakeClient:
    def __init__(self, status="not_affected", error=None, callback=None):
        self.status = status
        self.error = error
        self.callback = callback
        self.targets = None

    def query_context_integrity(self, targets):
        self.targets = tuple(targets)
        if self.callback:
            self.callback()
        if self.error:
            raise self.error
        return assessment(self.targets, self.status)


class ModelSpy:
    def __init__(self):
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return "model answer"


def run(status="not_affected", error=None, docs=None, callback=None):
    model = ModelSpy()
    client = FakeClient(status=status, error=error, callback=callback)
    docs = [document()] if docs is None else docs
    def invoke():
        return run_guarded_model_call(
            question="What does the policy say?",
            retrieve=lambda question: docs,
            client=client,
            serving_revision=SERVING,
            invoke_model=model,
        )
    return invoke, model, client


def test_not_affected_invokes_once_with_admitted_context():
    invoke, model, client = run()
    result = invoke()
    assert result.output == "model answer"
    assert result.admitted_document_count == 1
    assert result.admission_reason == "no_unresolved_impact"
    assert len(model.prompts) == 1
    assert "Known approved context." in model.prompts[0]
    assert client.targets == (
        SERVING, IntegrityRevision("source-a", "chunk-1", "chunk-1@v1")
    )


@pytest.mark.parametrize("status,reason", [
    ("unknown", "unknown_integrity"),
    ("affected", "unresolved_impact"),
    ("verified", "superseded_revision"),
])
def test_block_states_never_invoke_model(status, reason):
    invoke, model, _ = run(status=status)
    with pytest.raises(LangChainContextNotAdmittedError) as error:
        invoke()
    assert error.value.reason == reason
    assert model.prompts == []


@pytest.mark.parametrize("error", [
    SDEIntegrityHTTPError(503),
    SDEIntegrityProtocolError("bad protocol"),
    TimeoutError("SDE timeout"),
])
def test_integrity_failures_never_invoke_model(error):
    invoke, model, _ = run(error=error)
    with pytest.raises(type(error)):
        invoke()
    assert model.prompts == []


def test_missing_provenance_never_invokes_model():
    docs = [FakeDocument(id="chunk-1", metadata={}, page_content="unsafe")]
    invoke, model, client = run(docs=docs)
    with pytest.raises(ValueError):
        invoke()
    assert model.prompts == []
    assert client.targets is None


def test_empty_retrieval_never_invokes_model():
    invoke, model, client = run(docs=[])
    with pytest.raises(ValueError):
        invoke()
    assert model.prompts == []
    assert client.targets is None


def test_snapshot_is_stable_if_raw_document_mutates_during_admission():
    original = document()
    invoke, model, _ = run(
        docs=[original],
        callback=lambda: setattr(original, "page_content", "changed after retrieval"),
    )
    invoke()
    assert "Known approved context." in model.prompts[0]
    assert "changed after retrieval" not in model.prompts[0]


def test_invalid_question_refuses_retrieval_and_model():
    model = ModelSpy()
    called = []
    with pytest.raises(ValueError):
        run_guarded_model_call(
            question="  ",
            retrieve=lambda question: called.append(question),
            client=FakeClient(),
            serving_revision=SERVING,
            invoke_model=model,
        )
    assert not called
    assert not model.prompts
