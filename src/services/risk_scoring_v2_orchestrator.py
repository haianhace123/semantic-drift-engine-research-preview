from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.risk_assessment_service import (
    RiskAssessmentDependencyError,
    RiskAssessmentPersistenceResult,
    persist_risk_assessment_v2,
)
from src.services.risk_scoring_service import (
    CATEGORY_FACTORS,
    PAGE_TYPE_FACTORS,
    RISK_SEVERITY_FACTORS,
)


from src.services.risk_version import RISK_VERSION_V2


RISK_SCORE_VERSION_V2 = "v2"


class RiskScoringV2OrchestrationError(RuntimeError):
    """Base error for Risk v2 event orchestration."""


class RiskScoringV2NotReadyError(
    RiskScoringV2OrchestrationError
):
    """
    Raised when required upstream evidence is not ready.

    Not-ready is different from scoring failure. The worker may
    defer the event and retry after upstream topology persistence.
    """


class RiskScoringV2ValidationError(
    RiskScoringV2OrchestrationError
):
    """Raised when persisted event evidence is invalid."""


@dataclass(frozen=True)
class RiskScoringV2Inputs:
    semantic_magnitude: float
    legacy_impact: float
    semantic_severity: float
    page_context: float
    confidence: float


@dataclass(frozen=True)
class RiskScoringV2OrchestrationResult:
    event_id: str
    assessment_id: str
    risk_version: str
    assessment_created: bool

    score: float
    score_percent: float

    semantic_magnitude_contribution: float
    legacy_impact_contribution: float
    semantic_severity_contribution: float
    page_context_contribution: float
    confidence_contribution: float

    topology_score: float
    topology_contribution: float
    topology_shift_component: float
    topology_centrality_component: float
    topology_downstream_component: float

    has_topology_signal: bool
    has_previous_topology_state: bool


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
        raise RiskScoringV2ValidationError(
            f"{name} must be numeric."
        ) from exc

    if not math.isfinite(result):
        raise RiskScoringV2ValidationError(
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
        raise RiskScoringV2ValidationError(
            f"{name} must be between "
            f"{minimum} and {maximum}."
        )

    return result


def _normalize_confidence(
    value: Any,
) -> float:
    if value is None:
        return 0.0

    confidence = _finite_float(
        value,
        name="confidence",
    )

    if confidence < 0.0:
        raise RiskScoringV2ValidationError(
            "confidence cannot be negative."
        )

    if confidence <= 1.0:
        return confidence

    if confidence <= 100.0:
        return confidence / 100.0

    raise RiskScoringV2ValidationError(
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

    return float(
        RISK_SEVERITY_FACTORS.get(
            normalized,
            0.0,
        )
    )


def _page_context_factor(
    event: SemanticDriftEvent,
) -> float:
    page_type = None

    monitored_page = getattr(
        event,
        "monitored_page",
        None,
    )

    if monitored_page is not None:
        raw_page_type = getattr(
            monitored_page,
            "page_type",
            None,
        )

        if raw_page_type is not None:
            page_type = str(
                getattr(
                    raw_page_type,
                    "value",
                    raw_page_type,
                )
            ).strip().lower()

    category = getattr(
        event,
        "category",
        None,
    )

    normalized_category = (
        str(category).strip().lower()
        if category
        else None
    )

    page_factor = float(
        PAGE_TYPE_FACTORS.get(
            page_type or "",
            0.0,
        )
    )

    category_factor = float(
        CATEGORY_FACTORS.get(
            normalized_category or "",
            0.0,
        )
    )

    return max(
        page_factor,
        category_factor,
    )


def build_risk_v2_inputs(
    event: SemanticDriftEvent,
) -> RiskScoringV2Inputs:
    """
    Build normalized deterministic Risk v2 inputs from persisted
    SemanticDriftEvent evidence.

    This intentionally reuses the same factor tables as Risk v1
    so the semantic meaning of severity/context does not silently
    diverge between scoring versions.
    """
    if getattr(
        event,
        "id",
        None,
    ) is None:
        raise RiskScoringV2ValidationError(
            "event.id is required."
        )

    if getattr(
        event,
        "organization_id",
        None,
    ) is None:
        raise RiskScoringV2ValidationError(
            "event.organization_id is required."
        )

    if getattr(event, "analysis_status", None) != "completed":
        raise RiskScoringV2NotReadyError(
            "Risk v2 requires completed semantic analysis."
        )

    if (
        getattr(
            event,
            "vector_analysis_status",
            None,
        )
        != "completed"
    ):
        raise RiskScoringV2NotReadyError(
            "Risk v2 requires completed vector analysis."
        )

    semantic_magnitude = getattr(
        event,
        "semantic_magnitude",
        None,
    )

    if semantic_magnitude is None:
        raise RiskScoringV2NotReadyError(
            "Risk v2 requires semantic_magnitude."
        )

    normalized_magnitude = _bounded_float(
        semantic_magnitude,
        name="semantic_magnitude",
        minimum=0.0,
        maximum=1.0,
    )

    impact_score = getattr(
        event,
        "impact_score",
        None,
    )

    if impact_score is None:
        impact_score = 0.0

    normalized_impact = (
        _bounded_float(
            impact_score,
            name="impact_score",
            minimum=0.0,
            maximum=100.0,
        )
        / 100.0
    )

    severity = _risk_severity_factor(
        getattr(
            event,
            "risk_level",
            None,
        )
    )

    context = _page_context_factor(
        event
    )

    confidence = _normalize_confidence(
        getattr(
            event,
            "confidence",
            None,
        )
    )

    return RiskScoringV2Inputs(
        semantic_magnitude=normalized_magnitude,
        legacy_impact=normalized_impact,
        semantic_severity=severity,
        page_context=context,
        confidence=confidence,
    )


def _build_components(
    assessment: RiskAssessmentPersistenceResult,
    inputs: RiskScoringV2Inputs,
) -> dict[str, Any]:
    return {
        "assessment_id": str(
            assessment.assessment_id
        ),
        "assessment_version": (
            assessment.risk_version
        ),
        "inputs": {
            "semantic_magnitude": (
                inputs.semantic_magnitude
            ),
            "legacy_impact": (
                inputs.legacy_impact
            ),
            "semantic_severity": (
                inputs.semantic_severity
            ),
            "page_context": (
                inputs.page_context
            ),
            "confidence": (
                inputs.confidence
            ),
        },
        "contributions": {
            "semantic_magnitude": (
                assessment
                .semantic_magnitude_contribution
            ),
            "legacy_impact": (
                assessment
                .legacy_impact_contribution
            ),
            "semantic_severity": (
                assessment
                .semantic_severity_contribution
            ),
            "page_context": (
                assessment
                .page_context_contribution
            ),
            "confidence": (
                assessment
                .confidence_contribution
            ),
            "topology": (
                assessment.topology_contribution
            ),
        },
        "topology": {
            "score": (
                assessment.topology_score
            ),
            "shift_component": (
                assessment
                .topology_shift_component
            ),
            "centrality_component": (
                assessment
                .topology_centrality_component
            ),
            "downstream_component": (
                assessment
                .topology_downstream_component
            ),
            "has_signal": (
                assessment.has_topology_signal
            ),
            "has_previous_state": (
                assessment
                .has_previous_topology_state
            ),
        },
        "score": assessment.score,
        "score_percent": round(
            assessment.score * 100.0,
            2,
        ),
    }


class RiskScoringV2Service:
    """
    Event-level orchestration for deterministic Risk v2.

    Responsibilities:
      - validate vector readiness;
      - normalize persisted event evidence;
      - require already-persisted topology evidence;
      - persist immutable deterministic_v2 assessment;
      - project the assessment onto SemanticDriftEvent for
        backward-compatible API/UI reads.

    The caller owns commit/rollback.
    """

    def score_event(
        self,
        db: Session,
        event: SemanticDriftEvent,
    ) -> RiskScoringV2OrchestrationResult:
        inputs = build_risk_v2_inputs(
            event
        )

        try:
            assessment = (
                persist_risk_assessment_v2(
                    db,
                    event=event,
                    semantic_magnitude=(
                        inputs.semantic_magnitude
                    ),
                    legacy_impact=(
                        inputs.legacy_impact
                    ),
                    semantic_severity=(
                        inputs.semantic_severity
                    ),
                    page_context=(
                        inputs.page_context
                    ),
                    confidence=inputs.confidence,
                )
            )
        except RiskAssessmentDependencyError as exc:
            raise RiskScoringV2NotReadyError(
                str(exc)
            ) from exc

        score_percent = round(
            assessment.score * 100.0,
            2,
        )

        event.risk_score = score_percent
        event.risk_score_version = (
            RISK_SCORE_VERSION_V2
        )
        event.risk_score_components = (
            _build_components(
                assessment,
                inputs,
            )
        )

        event.risk_scoring_status = (
            "completed"
        )
        event.risk_scoring_error = None

        current_attempts = int(
            getattr(
                event,
                "risk_scoring_attempts",
                0,
            )
            or 0
        )

        event.risk_scoring_attempts = (
            current_attempts + 1
        )

        event.risk_scored_at = utc_now()

        db.add(event)

        return RiskScoringV2OrchestrationResult(
            event_id=str(event.id),
            assessment_id=str(
                assessment.assessment_id
            ),
            risk_version=RISK_VERSION_V2,
            assessment_created=(
                assessment.created
            ),
            score=assessment.score,
            score_percent=score_percent,
            semantic_magnitude_contribution=(
                assessment
                .semantic_magnitude_contribution
            ),
            legacy_impact_contribution=(
                assessment
                .legacy_impact_contribution
            ),
            semantic_severity_contribution=(
                assessment
                .semantic_severity_contribution
            ),
            page_context_contribution=(
                assessment
                .page_context_contribution
            ),
            confidence_contribution=(
                assessment
                .confidence_contribution
            ),
            topology_score=(
                assessment.topology_score
            ),
            topology_contribution=(
                assessment.topology_contribution
            ),
            topology_shift_component=(
                assessment
                .topology_shift_component
            ),
            topology_centrality_component=(
                assessment
                .topology_centrality_component
            ),
            topology_downstream_component=(
                assessment
                .topology_downstream_component
            ),
            has_topology_signal=(
                assessment.has_topology_signal
            ),
            has_previous_topology_state=(
                assessment
                .has_previous_topology_state
            ),
        )