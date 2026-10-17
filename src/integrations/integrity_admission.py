from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.integrations.integrity_client import ContextIntegrityAssessment


UnknownIntegrityPolicy = Literal["proceed", "block"]
IntegrityAdmissionOutcome = Literal["proceed", "block"]
IntegrityAdmissionReason = Literal[
    "no_unresolved_impact",
    "unknown_integrity",
    "unresolved_impact",
    "superseded_revision",
]


@dataclass(frozen=True, slots=True)
class IntegrityAdmissionDecision:
    outcome: IntegrityAdmissionOutcome
    reason: IntegrityAdmissionReason


@dataclass(frozen=True, slots=True)
class IntegrityAdmissionPolicy:
    unknown: UnknownIntegrityPolicy

    def __post_init__(self) -> None:
        if self.unknown not in {"proceed", "block"}:
            raise ValueError(
                "unknown must be either proceed or block."
            )


def evaluate_integrity_admission(
    assessment: ContextIntegrityAssessment,
    *,
    policy: IntegrityAdmissionPolicy,
) -> IntegrityAdmissionDecision:
    """Apply caller-owned admission policy to factual integrity evidence."""

    if assessment.summary.has_unresolved_impact:
        return IntegrityAdmissionDecision(
            outcome="block",
            reason="unresolved_impact",
        )

    # A verified *execution* clears an invalidation workflow, not the
    # validity of the historical target revision. That revision remains
    # superseded even after a successor is materialized and attested.
    # Never turn a successful repair into permission to serve stale bytes.
    if assessment.summary.verified_count > 0:
        return IntegrityAdmissionDecision(
            outcome="block",
            reason="superseded_revision",
        )

    if assessment.summary.has_unknown:
        return IntegrityAdmissionDecision(
            outcome=policy.unknown,
            reason="unknown_integrity",
        )

    return IntegrityAdmissionDecision(
        outcome="proceed",
        reason="no_unresolved_impact",
    )
