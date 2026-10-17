"""Read-only diagnostic of the uncalibrated confidence contribution in Risk v2/v3.

This is NOT a proposed replacement scoring model. Its counterfactual removes the
already-persisted contribution without redistribution, preserving the original
score components and risk-version identity.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from statistics import mean
from typing import Sequence
from uuid import UUID

from src.services.risk_classification_service import classify_risk_score
from src.services.risk_version import RISK_VERSION_V2, RISK_VERSION_V3

SUPPORTED_VERSIONS = (RISK_VERSION_V2, RISK_VERSION_V3)
MAX_CONFIDENCE_CONTRIBUTION = 0.10
MAX_ROWS_PER_VERSION = 5000


@dataclass(frozen=True)
class RiskConfidenceSample:
    risk_version: str
    score: float
    confidence_contribution: float


def _validated_unit(value: object, *, name: str, ceiling: float = 1.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(numeric) or not (0.0 <= numeric <= ceiling):
        raise ValueError(f"{name} must be between 0 and {ceiling}")
    return numeric


def summarize_confidence_exposure(
    samples: Sequence[RiskConfidenceSample],
    *,
    risk_version: str,
    truncated: bool = False,
) -> dict[str, object]:
    """Analyze a single persisted scoring version without changing its meaning."""
    if risk_version not in SUPPORTED_VERSIONS:
        raise ValueError("unsupported risk version")
    if not isinstance(truncated, bool):
        raise ValueError("truncated must be boolean")

    base_bands: Counter[str] = Counter()
    counterfactual_bands: Counter[str] = Counter()
    transitions: Counter[str] = Counter()
    contributions: list[float] = []
    changed = 0

    for row in samples:
        if row.risk_version != risk_version:
            raise ValueError("mixed risk versions in audit sample")
        score = _validated_unit(row.score, name="persisted score")
        component = _validated_unit(
            row.confidence_contribution,
            name="persisted confidence contribution",
            ceiling=MAX_CONFIDENCE_CONTRIBUTION + 1e-6,
        )
        # Scores are rounded to six decimals when persisted, so tolerate a
        # one-micro-unit discrepancy rather than quietly ignoring corruption.
        if component > score + 1e-6:
            raise ValueError("confidence contribution exceeds persisted score")
        without_confidence = max(0.0, score - component)
        original_band = classify_risk_score(score)
        adjusted_band = classify_risk_score(without_confidence)
        base_bands[original_band] += 1
        counterfactual_bands[adjusted_band] += 1
        contributions.append(component)
        if original_band != adjusted_band:
            changed += 1
            transitions[f"{original_band}_to_{adjusted_band}"] += 1

    n = len(contributions)
    ordered = sorted(contributions)
    p95_index = math.ceil(0.95 * n) - 1 if n else 0
    return {
        "risk_version": risk_version,
        "assessments_evaluated": n,
        "truncated": truncated,
        "sample_scope": "most_recent_assessments_per_version",
        "mean_confidence_contribution_points": round(mean(contributions) * 100, 4) if n else None,
        "p95_confidence_contribution_points": round(ordered[p95_index] * 100, 4) if n else None,
        "max_confidence_contribution_points": round(ordered[-1] * 100, 4) if n else None,
        "severity_changes_if_contribution_removed": changed,
        "severity_change_rate": round(changed / n, 6) if n else None,
        "baseline_severity_counts": dict(sorted(base_bands.items())),
        "counterfactual_severity_counts": dict(sorted(counterfactual_bands.items())),
        "severity_transitions": dict(sorted(transitions.items())),
    }


def load_scoped_assessments(
    db: object,
    *,
    organization_id: UUID,
    risk_version: str,
    limit: int,
) -> tuple[list[RiskConfidenceSample], bool]:
    """Query only one tenant and risk version, with a deterministic hard cap."""
    if risk_version not in SUPPORTED_VERSIONS:
        raise ValueError("unsupported risk version")
    if not isinstance(organization_id, UUID):
        raise ValueError("organization_id must be UUID")
    if type(limit) is not int or not 1 <= limit <= MAX_ROWS_PER_VERSION:
        raise ValueError("limit must be between 1 and 5000")

    from sqlalchemy import select
    from src.models.risk_assessment import RiskAssessment

    stmt = (
        select(RiskAssessment.score, RiskAssessment.confidence_contribution)
        .where(
            RiskAssessment.organization_id == organization_id,
            RiskAssessment.risk_version == risk_version,
        )
        .order_by(RiskAssessment.assessed_at.desc(), RiskAssessment.id.desc())
        .limit(limit + 1)
    )
    rows = db.execute(stmt).all()
    truncated = len(rows) > limit
    samples = [
        RiskConfidenceSample(
            risk_version=risk_version,
            score=score,
            confidence_contribution=component,
        )
        for score, component in rows[:limit]
    ]
    return samples, truncated
