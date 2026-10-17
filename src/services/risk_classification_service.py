"""
Canonical deterministic Risk v2 classification helpers.

This module owns normalization and severity-band semantics shared by
downstream intelligence consumers such as What You Missed and Alert
candidate generation.

It intentionally performs:
- no database queries;
- no database writes;
- no external API calls;
- no mutation.

Persisted RiskAssessment.score uses the normalized 0..1 representation.
Some existing read models also tolerate the event-facing 0..100 form, so
normalization accepts both representations deterministically.
"""

from __future__ import annotations

from typing import Literal


RiskSeverity = Literal[
    "low",
    "medium",
    "high",
    "critical",
]


RISK_CRITICAL_THRESHOLD = 0.85
RISK_HIGH_THRESHOLD = 0.65
RISK_MEDIUM_THRESHOLD = 0.40


def clamp_unit(
    value: float,
) -> float:
    """Clamp a numeric value to the inclusive 0..1 range."""
    return max(
        0.0,
        min(
            1.0,
            float(value),
        ),
    )


def normalize_risk_score(
    score: float | None,
) -> float:
    """
    Normalize a Risk v2 score to 0..1.

    RiskAssessment.score is persisted in the 0..1 range.

    SemanticDriftEvent.risk_score currently exposes the corresponding
    percentage form in the 0..100 range. Accepting both representations
    keeps downstream deterministic consumers compatible without
    duplicating normalization logic.
    """
    if score is None:
        return 0.0

    value = float(
        score
    )

    if value <= 1.0:
        return clamp_unit(
            value
        )

    return clamp_unit(
        value / 100.0
    )


def classify_risk_score(
    score: float | None,
) -> RiskSeverity:
    """
    Map Risk v2 score to the canonical downstream severity band.

    Thresholds intentionally preserve the existing What You Missed v1
    semantics:

        >= 0.85    critical
        >= 0.65    high
        >= 0.40    medium
        <  0.40    low
    """
    value = normalize_risk_score(
        score
    )

    if value >= RISK_CRITICAL_THRESHOLD:
        return "critical"

    if value >= RISK_HIGH_THRESHOLD:
        return "high"

    if value >= RISK_MEDIUM_THRESHOLD:
        return "medium"

    return "low"