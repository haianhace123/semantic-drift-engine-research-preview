from __future__ import annotations

import pytest

pytest.importorskip("langchain_core.documents")
pytest.importorskip("langchain_core.embeddings")
pytest.importorskip("langchain_postgres")

from experiments.external_langchain_rag import _proof_admission_policy
from src.integrations.integrity_admission import evaluate_integrity_admission
from src.integrations.integrity_client import (
    ContextIntegrityAssessment,
    ContextIntegritySummary,
)


def _assessment(*, unknown: bool, unresolved: bool) -> ContextIntegrityAssessment:
    return ContextIntegrityAssessment(
        assessments=(),
        summary=ContextIntegritySummary(
            target_count=1,
            unknown_count=int(unknown),
            not_affected_count=int(not unknown and not unresolved),
            affected_count=int(unresolved),
            verified_count=0,
            has_unknown=unknown,
            has_unresolved_impact=unresolved,
        ),
    )


def test_external_rag_blocks_unknown_by_default() -> None:
    decision = evaluate_integrity_admission(
        _assessment(unknown=True, unresolved=False),
        policy=_proof_admission_policy(allow_unknown_for_lineage_proof=False),
    )
    assert decision.outcome == "block"
    assert decision.reason == "unknown_integrity"


def test_external_rag_lineage_proof_requires_explicit_unknown_opt_in() -> None:
    decision = evaluate_integrity_admission(
        _assessment(unknown=True, unresolved=False),
        policy=_proof_admission_policy(allow_unknown_for_lineage_proof=True),
    )
    assert decision.outcome == "proceed"
    assert decision.reason == "unknown_integrity"


@pytest.mark.parametrize("allow_unknown", [True, False])
def test_unresolved_impact_blocks_in_both_modes(allow_unknown: bool) -> None:
    decision = evaluate_integrity_admission(
        _assessment(unknown=False, unresolved=True),
        policy=_proof_admission_policy(
            allow_unknown_for_lineage_proof=allow_unknown
        ),
    )
    assert decision.outcome == "block"
    assert decision.reason == "unresolved_impact"


def test_safe_context_proceeds_without_opt_in() -> None:
    decision = evaluate_integrity_admission(
        _assessment(unknown=False, unresolved=False),
        policy=_proof_admission_policy(allow_unknown_for_lineage_proof=False),
    )
    assert decision.outcome == "proceed"
    assert decision.reason == "no_unresolved_impact"


def test_invalid_opt_in_flag_is_rejected() -> None:
    with pytest.raises(ValueError, match="boolean"):
        _proof_admission_policy(allow_unknown_for_lineage_proof="yes")  # type: ignore[arg-type]
