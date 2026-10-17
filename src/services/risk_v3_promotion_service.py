from __future__ import annotations

import math
from dataclasses import dataclass

from src.services.risk_shadow_evaluation_service import (
    PersistedRiskShadowEvaluation,
    RiskShadowEvaluation,
)


@dataclass(frozen=True)
class RiskV3PromotionPolicy:
    minimum_paired_events: int
    minimum_pair_coverage: float
    minimum_previous_state_pairs: int
    maximum_classification_change_rate: float


@dataclass(frozen=True)
class RiskV3PromotionCheck:
    name: str
    passed: bool
    observed: int | float
    required: str


@dataclass(frozen=True)
class RiskV3PromotionReadiness:
    ready: bool
    checks: tuple[RiskV3PromotionCheck, ...]


@dataclass(frozen=True)
class RiskV3QualificationEvidence:
    previous_state_pairs: int
    exercised_topology_pairs: int
    divergent_pairs: int
    topology_exercise_observed: bool


def summarize_risk_v3_qualification_evaluation(
    evaluation: RiskShadowEvaluation | None,
) -> RiskV3QualificationEvidence:
    if evaluation is None:
        return RiskV3QualificationEvidence(
            previous_state_pairs=0,
            exercised_topology_pairs=0,
            divergent_pairs=0,
            topology_exercise_observed=False,
        )

    topology = evaluation.topology

    return RiskV3QualificationEvidence(
        previous_state_pairs=topology.previous_state_pairs,
        exercised_topology_pairs=topology.exercised_topology_pairs,
        divergent_pairs=topology.divergent_pairs,
        topology_exercise_observed=(
            topology.exercised_topology_pairs > 0
        ),
    )


def summarize_risk_v3_qualification_evidence(
    shadow: PersistedRiskShadowEvaluation,
) -> RiskV3QualificationEvidence:
    return summarize_risk_v3_qualification_evaluation(
        shadow.evaluation
    )


def _require_positive_integer(
    value: object,
    *,
    name: str,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"{name} must be a positive integer"
        )

    if value < 1:
        raise ValueError(
            f"{name} must be a positive integer"
        )


def _validate_policy(policy: RiskV3PromotionPolicy) -> None:
    _require_positive_integer(
        policy.minimum_paired_events,
        name="minimum_paired_events",
    )

    if (
        not math.isfinite(policy.minimum_pair_coverage)
        or policy.minimum_pair_coverage < 0.0
        or policy.minimum_pair_coverage > 1.0
    ):
        raise ValueError(
            "minimum_pair_coverage must be finite and between 0 and 1"
        )

    _require_positive_integer(
        policy.minimum_previous_state_pairs,
        name="minimum_previous_state_pairs",
    )

    if (
        not math.isfinite(
            policy.maximum_classification_change_rate
        )
        or policy.maximum_classification_change_rate < 0.0
        or policy.maximum_classification_change_rate > 1.0
    ):
        raise ValueError(
            "maximum_classification_change_rate must be finite "
            "and between 0 and 1"
        )


def evaluate_risk_v3_promotion_readiness(
    shadow: PersistedRiskShadowEvaluation,
    *,
    policy: RiskV3PromotionPolicy,
) -> RiskV3PromotionReadiness:
    _validate_policy(policy)

    coverage = shadow.coverage
    evaluation = shadow.evaluation

    paired_count = (
        evaluation.paired_count
        if evaluation is not None
        else 0
    )

    previous_state_pairs = (
        evaluation.topology.previous_state_pairs
        if evaluation is not None
        else 0
    )

    non_topology_mismatches = (
        evaluation.non_topology_contribution_mismatches
        if evaluation is not None
        else 0
    )

    topology_state_mismatches = (
        (
            evaluation.topology.signal_state_mismatches
            + evaluation.topology.previous_state_mismatches
        )
        if evaluation is not None
        else 0
    )

    classification_changes = (
        evaluation.movement.classification_changed
        if evaluation is not None
        else 0
    )

    classification_change_rate = (
        classification_changes / paired_count
        if paired_count > 0
        else 0.0
    )

    checks = (
        RiskV3PromotionCheck(
            name="paired_events",
            passed=paired_count >= policy.minimum_paired_events,
            observed=paired_count,
            required=f">={policy.minimum_paired_events}",
        ),
        RiskV3PromotionCheck(
            name="pair_coverage",
            passed=(
                coverage.pair_coverage
                >= policy.minimum_pair_coverage
            ),
            observed=coverage.pair_coverage,
            required=f">={policy.minimum_pair_coverage}",
        ),
        RiskV3PromotionCheck(
            name="missing_versions",
            passed=(
                coverage.v2_only == 0
                and coverage.v3_only == 0
            ),
            observed=coverage.v2_only + coverage.v3_only,
            required="0",
        ),
        RiskV3PromotionCheck(
            name="non_topology_invariance",
            passed=non_topology_mismatches == 0,
            observed=non_topology_mismatches,
            required="0",
        ),
        RiskV3PromotionCheck(
            name="topology_state_invariance",
            passed=topology_state_mismatches == 0,
            observed=topology_state_mismatches,
            required="0",
        ),
        RiskV3PromotionCheck(
            name="previous_topology_evidence",
            passed=(
                previous_state_pairs
                >= policy.minimum_previous_state_pairs
            ),
            observed=previous_state_pairs,
            required=f">={policy.minimum_previous_state_pairs}",
        ),
        RiskV3PromotionCheck(
            name="classification_change_rate",
            passed=(
                classification_change_rate
                <= policy.maximum_classification_change_rate
            ),
            observed=round(
                classification_change_rate,
                6,
            ),
            required=(
                "<="
                f"{policy.maximum_classification_change_rate}"
            ),
        ),
    )

    return RiskV3PromotionReadiness(
        ready=all(check.passed for check in checks),
        checks=checks,
    )
