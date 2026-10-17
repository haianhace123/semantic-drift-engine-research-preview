from __future__ import annotations

import pytest

from src.integrations.integrity_admission import (
    IntegrityAdmissionPolicy,
    evaluate_integrity_admission,
)
from src.integrations.integrity_client import (
    ContextIntegrityAssessment,
    ContextIntegritySummary,
)


def _assessment(
    *,
    unknown_count: int = 0,
    affected_count: int = 0,
    not_affected_count: int = 0,
    verified_count: int = 0,
) -> ContextIntegrityAssessment:
    target_count = (
        unknown_count
        + affected_count
        + not_affected_count
        + verified_count
    )
    return ContextIntegrityAssessment(
        assessments=(),
        summary=ContextIntegritySummary(
            target_count=target_count,
            unknown_count=unknown_count,
            not_affected_count=not_affected_count,
            affected_count=affected_count,
            verified_count=verified_count,
            has_unknown=unknown_count > 0,
            has_unresolved_impact=affected_count > 0,
        ),
    )


def test_unresolved_impact_blocks_even_when_unknown_policy_proceeds() -> None:
    decision = evaluate_integrity_admission(
        _assessment(affected_count=1, unknown_count=1),
        policy=IntegrityAdmissionPolicy(unknown="proceed"),
    )

    assert decision.outcome == "block"
    assert decision.reason == "unresolved_impact"


def test_unknown_requires_explicit_caller_policy() -> None:
    assessment = _assessment(unknown_count=1)

    proceed = evaluate_integrity_admission(
        assessment,
        policy=IntegrityAdmissionPolicy(unknown="proceed"),
    )
    block = evaluate_integrity_admission(
        assessment,
        policy=IntegrityAdmissionPolicy(unknown="block"),
    )

    assert proceed.outcome == "proceed"
    assert proceed.reason == "unknown_integrity"
    assert block.outcome == "block"
    assert block.reason == "unknown_integrity"


def test_known_no_action_context_without_unresolved_impact_proceeds() -> None:
    decision = evaluate_integrity_admission(
        _assessment(not_affected_count=2),
        policy=IntegrityAdmissionPolicy(unknown="block"),
    )

    assert decision.outcome == "proceed"
    assert decision.reason == "no_unresolved_impact"


@pytest.mark.parametrize("unknown_policy", ["block", "proceed"])
def test_successful_repair_does_not_readmit_superseded_revision(unknown_policy: str) -> None:
    decision = evaluate_integrity_admission(
        _assessment(verified_count=1, not_affected_count=1),
        policy=IntegrityAdmissionPolicy(unknown=unknown_policy),
    )

    assert decision.outcome == "block"
    assert decision.reason == "superseded_revision"


def test_verified_target_blocks_even_when_unknown_is_explicitly_allowed() -> None:
    decision = evaluate_integrity_admission(
        _assessment(verified_count=1, unknown_count=1),
        policy=IntegrityAdmissionPolicy(unknown="proceed"),
    )

    assert decision.outcome == "block"
    assert decision.reason == "superseded_revision"


def test_unknown_policy_rejects_unsupported_value() -> None:
    with pytest.raises(ValueError, match="unknown"):
        IntegrityAdmissionPolicy(unknown="ignore")  # type: ignore[arg-type]
