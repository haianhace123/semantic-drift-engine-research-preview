"""Reference consumer: retrieval -> SDE admission -> model invocation.

This reference enforces request-time identity admission and uses immutable
context returned by the gate, including content checks against SDE-issued content-addressed chunk revision digests. It is not
independent authority-backed physical verification, an atomic read across
stores, or a replacement for verified-serving controls in production.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.integrations.integrity_client import IntegrityRevision
from src.integrations.langchain.context_admission import admit_langchain_context
from src.integrations.langchain.context_integrity import ContextIntegrityClientLike
from src.integrations.langchain.lineage import LangChainDocumentLike


@dataclass(frozen=True, slots=True)
class GuardedModelResult:
    output: str
    admitted_document_count: int
    admission_reason: str


def run_guarded_model_call(
    *,
    question: str,
    retrieve: Callable[[str], Sequence[LangChainDocumentLike]],
    client: ContextIntegrityClientLike,
    serving_revision: IntegrityRevision,
    invoke_model: Callable[[str], str],
) -> GuardedModelResult:
    """Only invoke the model after a default fail-closed admission decision.

    The model receives a prompt built only from the immutable snapshots
    returned by the request-time admission gate. The application must
    not separately invoke a model with the unguarded retrieval result.

    This protects the control flow of *this* reference consumer. The caller
    cannot treat its result as proof that retrieved page_content matches the
    independent authority snapshot or that no future authority change occurs.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")

    raw = tuple(retrieve(question))
    admitted = admit_langchain_context(
        client=client,
        serving_revision=serving_revision,
        retrieved_documents=raw,
        # No opt-in to unknown=proceed is exposed by this serving boundary.
    )
    context = "\n\n".join(doc.page_content for doc in admitted.documents)
    prompt = (
        "Answer the question using only the supplied admitted context.\n"
        f"Question: {question}\n"
        f"Admitted context:\n{context}"
    )
    output = invoke_model(prompt)
    return GuardedModelResult(
        output=output,
        admitted_document_count=len(admitted.documents),
        admission_reason=admitted.admission.reason,
    )
