from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.risk_assessment_service import (
    RiskAssessmentDependencyError,
    persist_risk_assessment_v3,
)
from src.services.risk_scoring_v2_orchestrator import (
    RiskScoringV2Inputs,
    build_risk_v2_inputs,
)
from src.services.risk_version import RISK_VERSION_V3


class RiskScoringV3ShadowNotReadyError(RuntimeError):
    """Risk v3 shadow evidence is not ready for persistence."""


@dataclass(frozen=True)
class RiskScoringV3ShadowResult:
    event_id: str
    assessment_id: str
    risk_version: str
    assessment_created: bool
    score: float


class RiskScoringV3ShadowService:
    '''
    Persist deterministic Risk v3 as shadow evidence.

    Risk v3 is not authoritative yet. This service must not mutate
    SemanticDriftEvent risk projection or risk-scoring lifecycle state.
    The caller owns commit/rollback.
    '''

    def score_event(
        self,
        db: Session,
        event: SemanticDriftEvent,
        *,
        inputs: RiskScoringV2Inputs | None = None,
    ) -> RiskScoringV3ShadowResult:
        normalized_inputs = (
            inputs
            if inputs is not None
            else build_risk_v2_inputs(event)
        )

        try:
            assessment = persist_risk_assessment_v3(
                db,
                event=event,
                semantic_magnitude=(
                    normalized_inputs.semantic_magnitude
                ),
                legacy_impact=normalized_inputs.legacy_impact,
                semantic_severity=(
                    normalized_inputs.semantic_severity
                ),
                page_context=normalized_inputs.page_context,
                confidence=normalized_inputs.confidence,
            )
        except RiskAssessmentDependencyError as exc:
            raise RiskScoringV3ShadowNotReadyError(str(exc)) from exc

        return RiskScoringV3ShadowResult(
            event_id=str(event.id),
            assessment_id=str(assessment.assessment_id),
            risk_version=RISK_VERSION_V3,
            assessment_created=assessment.created,
            score=assessment.score,
        )
