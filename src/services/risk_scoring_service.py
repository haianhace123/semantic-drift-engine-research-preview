"""
Deterministic risk scoring for semantic drift events.

Risk Scoring Engine v1 deliberately does not call an LLM or any
external provider. Given the same persisted event evidence, it
produces the same score.

V1 inputs:
    semantic magnitude     40%
    legacy impact          25%
    semantic severity      15%
    page context           10%
    confidence             10%

Graph centrality and true downstream-impact topology are reserved
for a later scoring version once the entity relationship graph
exists.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from src.models.semantic_drift_event import (
    RISK_SCORE_VERSION_V1,
    RISK_SCORING_STATUS_COMPLETED,
    RISK_SCORING_STATUS_FAILED,
    RISK_SCORING_STATUS_PENDING,
    RISK_SCORING_STATUS_PROCESSING,
    SemanticDriftEvent,
)


MAGNITUDE_WEIGHT = 40.0
IMPACT_WEIGHT = 25.0
SEVERITY_WEIGHT = 15.0
CONTEXT_WEIGHT = 10.0
CONFIDENCE_WEIGHT = 10.0


RISK_SEVERITY_FACTORS = {
    "critical": 1.0,
    "high": 1.0,
    "medium": 0.60,
    "low": 0.25,
    "unknown": 0.0,
}


PAGE_TYPE_FACTORS = {
    "sla": 1.0,
    "tos": 0.95,
    "privacy": 0.95,
    "pricing": 0.85,
    "docs": 0.50,
}


CATEGORY_FACTORS = {
    "security": 1.0,
    "legal": 0.95,
    "policy": 0.85,
    "pricing": 0.85,
    "feature": 0.60,
}


class RiskScoringError(RuntimeError):
    """Base exception for deterministic risk scoring."""


class RiskScoringNotReadyError(RiskScoringError):
    """
    Raised when required upstream vector evidence is not ready.

    Not-ready events remain pending rather than being marked failed.
    """


class RiskScoringValidationError(RiskScoringError):
    """Raised when persisted scoring inputs are invalid."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _finite_float(
    value: Any,
    *,
    name: str,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RiskScoringValidationError(
            f"{name} must be numeric."
        ) from exc

    if not math.isfinite(result):
        raise RiskScoringValidationError(
            f"{name} must be finite."
        )

    return result


def _bounded_float(
    value: Any,
    *,
    name: str,
    minimum: float,
    maximum: float,
) -> float:
    result = _finite_float(
        value,
        name=name,
    )

    if result < minimum or result > maximum:
        raise RiskScoringValidationError(
            f"{name} must be between "
            f"{minimum} and {maximum}."
        )

    return result


def _normalize_confidence(
    value: Any,
) -> float:
    """
    Normalize confidence to [0, 1].

    Current semantic analysis is expected to use [0, 1].
    The <=100 branch is intentionally accepted for defensive
    compatibility with historical percentage-style values.
    """
    if value is None:
        return 0.0

    confidence = _finite_float(
        value,
        name="confidence",
    )

    if confidence < 0.0:
        raise RiskScoringValidationError(
            "confidence cannot be negative."
        )

    if confidence <= 1.0:
        return confidence

    if confidence <= 100.0:
        return confidence / 100.0

    raise RiskScoringValidationError(
        "confidence must be between 0 and 1 "
        "or between 0 and 100."
    )


def _risk_severity_factor(
    risk_level: Any,
) -> float:
    normalized = str(
        risk_level
        or "unknown"
    ).strip().lower()

    return RISK_SEVERITY_FACTORS.get(
        normalized,
        0.0,
    )


def _page_context_factor(
    event: SemanticDriftEvent,
) -> tuple[float, dict[str, Any]]:
    """
    Determine context sensitivity from persisted page/category data.

    The stronger of page_type and semantic category is used. This
    avoids double-counting two labels that often describe the same
    business sensitivity.
    """
    page_type = None

    if event.monitored_page is not None:
        raw_page_type = event.monitored_page.page_type

        if raw_page_type is not None:
            page_type = str(
                getattr(
                    raw_page_type,
                    "value",
                    raw_page_type,
                )
            ).strip().lower()

    category = (
        str(event.category).strip().lower()
        if event.category
        else None
    )

    page_factor = PAGE_TYPE_FACTORS.get(
        page_type or "",
        0.0,
    )

    category_factor = CATEGORY_FACTORS.get(
        category or "",
        0.0,
    )

    factor = max(
        page_factor,
        category_factor,
    )

    return (
        factor,
        {
            "page_type": page_type,
            "category": category,
            "page_type_factor": page_factor,
            "category_factor": category_factor,
        },
    )


def calculate_risk_score(
    *,
    semantic_magnitude: Any,
    impact_score: Any,
    risk_level: Any,
    confidence: Any,
    context_factor: Any,
) -> dict[str, Any]:
    """
    Calculate Risk Scoring Engine v1.

    Returns both the final 0..100 score and an auditable component
    breakdown.
    """
    magnitude = _bounded_float(
        semantic_magnitude,
        name="semantic_magnitude",
        minimum=0.0,
        maximum=1.0,
    )

    impact = _bounded_float(
        impact_score,
        name="impact_score",
        minimum=0.0,
        maximum=100.0,
    )

    normalized_confidence = (
        _normalize_confidence(
            confidence
        )
    )

    normalized_context = _bounded_float(
        context_factor,
        name="context_factor",
        minimum=0.0,
        maximum=1.0,
    )

    severity_factor = (
        _risk_severity_factor(
            risk_level
        )
    )

    magnitude_points = (
        magnitude
        * MAGNITUDE_WEIGHT
    )

    impact_points = (
        impact
        / 100.0
        * IMPACT_WEIGHT
    )

    severity_points = (
        severity_factor
        * SEVERITY_WEIGHT
    )

    context_points = (
        normalized_context
        * CONTEXT_WEIGHT
    )

    confidence_points = (
        normalized_confidence
        * CONFIDENCE_WEIGHT
    )

    score = (
        magnitude_points
        + impact_points
        + severity_points
        + context_points
        + confidence_points
    )

    score = max(
        0.0,
        min(
            100.0,
            score,
        ),
    )

    score = round(
        score,
        2,
    )

    return {
        "risk_score": score,
        "version": RISK_SCORE_VERSION_V1,
        "components": {
            "semantic_magnitude": {
                "value": magnitude,
                "weight": MAGNITUDE_WEIGHT,
                "points": round(
                    magnitude_points,
                    2,
                ),
            },
            "impact": {
                "value": impact,
                "weight": IMPACT_WEIGHT,
                "points": round(
                    impact_points,
                    2,
                ),
            },
            "severity": {
                "value": str(
                    risk_level
                    or "unknown"
                ).strip().lower(),
                "factor": severity_factor,
                "weight": SEVERITY_WEIGHT,
                "points": round(
                    severity_points,
                    2,
                ),
            },
            "context": {
                "factor": normalized_context,
                "weight": CONTEXT_WEIGHT,
                "points": round(
                    context_points,
                    2,
                ),
            },
            "confidence": {
                "value": normalized_confidence,
                "weight": CONFIDENCE_WEIGHT,
                "points": round(
                    confidence_points,
                    2,
                ),
            },
        },
    }


class RiskScoringService:
    """
    Persist deterministic risk scoring for SemanticDriftEvent.
    """

    def score_event(
        self,
        db: Session,
        event: SemanticDriftEvent,
    ) -> SemanticDriftEvent:
        """
        Score one event.

        Lifecycle:

            pending/failed
                -> processing
                -> completed

        Missing upstream vector evidence:

            pending/failed
                -> pending

        Invalid inputs or persistence/calculation failures:

            pending/processing/failed
                -> failed
        """
        if (
            event.risk_scoring_status
            == RISK_SCORING_STATUS_COMPLETED
            and event.risk_score_version
            == RISK_SCORE_VERSION_V1
            and event.risk_score is not None
        ):
            return event

        if (
            event.risk_scoring_status
            == RISK_SCORING_STATUS_PROCESSING
        ):
            return event

        if (
            event.vector_analysis_status
            != "completed"
            or event.semantic_magnitude is None
        ):
            return self._mark_pending(
                db,
                event,
                (
                    "Vector analysis is not completed "
                    f"for event={event.id}."
                ),
            )

        try:
            context_factor, context_metadata = (
                _page_context_factor(
                    event
                )
            )

            event.risk_scoring_status = (
                RISK_SCORING_STATUS_PROCESSING
            )

            event.risk_scoring_error = None

            event.risk_scoring_attempts = (
                int(
                    event.risk_scoring_attempts
                    or 0
                )
                + 1
            )

            db.add(event)
            db.commit()
            db.refresh(event)

            result = calculate_risk_score(
                semantic_magnitude=(
                    event.semantic_magnitude
                ),
                impact_score=(
                    event.impact_score
                ),
                risk_level=(
                    event.risk_level
                ),
                confidence=(
                    event.confidence
                ),
                context_factor=(
                    context_factor
                ),
            )

            components = dict(
                result["components"]
            )

            components["context"].update(
                context_metadata
            )

            event.risk_score = (
                result["risk_score"]
            )

            event.risk_score_version = (
                result["version"]
            )

            event.risk_score_components = (
                components
            )

            event.risk_scoring_status = (
                RISK_SCORING_STATUS_COMPLETED
            )

            event.risk_scoring_error = None
            event.risk_scored_at = utc_now()

            db.add(event)
            db.commit()
            db.refresh(event)

            return event

        except RiskScoringNotReadyError:
            raise

        except Exception as exc:
            db.rollback()

            try:
                event.risk_score = None
                event.risk_score_version = None
                event.risk_score_components = None

                event.risk_scoring_status = (
                    RISK_SCORING_STATUS_FAILED
                )

                event.risk_scoring_error = (
                    str(exc)[:2000]
                )

                event.risk_scored_at = None

                db.add(event)
                db.commit()
                db.refresh(event)

            except Exception:
                db.rollback()

            raise

    def _mark_pending(
        self,
        db: Session,
        event: SemanticDriftEvent,
        reason: str,
    ) -> SemanticDriftEvent:
        event.risk_score = None
        event.risk_score_version = None
        event.risk_score_components = None

        event.risk_scoring_status = (
            RISK_SCORING_STATUS_PENDING
        )

        event.risk_scoring_error = (
            reason[:2000]
        )

        event.risk_scored_at = None

        db.add(event)
        db.commit()
        db.refresh(event)

        return event