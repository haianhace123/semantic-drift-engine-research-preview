from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from sqlalchemy.orm import Session

from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.event_topology_risk_service import (
    EventTopologyRiskEvidence,
    reduce_event_topology_risk_signal,
)
from src.services.event_topology_risk_v3_service import (
    EventTopologyRiskV3Evidence,
    reduce_event_topology_risk_signal_v3,
)
from src.services.graph_metrics_service import (
    OrganizationGraphMetrics,
    calculate_organization_graph_metrics_as_of,
)
from src.services.risk_scoring_v2_orchestrator import (
    RiskScoringV2ValidationError,
    build_risk_v2_inputs,
)
from src.services.risk_scoring_v2_service import (
    RiskScoreV2Input,
    calculate_risk_score_v2,
)
from src.services.risk_scoring_v3_service import (
    RiskScoreV3Input,
    TopologyRiskV3Input,
    calculate_risk_score_v3,
)
from src.services.risk_shadow_evaluation_service import (
    RiskShadowEvaluation,
    evaluate_risk_shadow_comparisons,
)
from src.services.risk_v3_promotion_service import (
    RiskV3QualificationEvidence,
    summarize_risk_v3_qualification_evaluation,
)
from src.services.risk_version_comparison_service import (
    RiskComparisonValue,
    RiskVersionComparison,
    compare_risk_values,
)
from src.services.topology_shift_service import (
    OrganizationTopologyShift,
    compare_graph_metrics,
)


@dataclass(frozen=True)
class RiskV3RequalificationResult:
    organization_id: uuid.UUID
    eligible_event_count: int
    paired_event_count: int
    invalid_event_count: int
    distinct_timestamp_count: int
    comparisons: tuple[
        RiskVersionComparison,
        ...,
    ]
    evaluation: RiskShadowEvaluation | None
    qualification_evidence: RiskV3QualificationEvidence


def _as_uuid(
    value: uuid.UUID | str,
) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value

    return uuid.UUID(str(value))


def _empty_graph_metrics(
    organization_id: uuid.UUID,
) -> OrganizationGraphMetrics:
    return OrganizationGraphMetrics(
        organization_id=organization_id,
        entity_count=0,
        active_edge_count=0,
        entities=(),
    )


def _topology_evidence_v2(
    shift: OrganizationTopologyShift,
) -> tuple[EventTopologyRiskEvidence, ...]:
    return tuple(
        EventTopologyRiskEvidence(
            shift_score=entity.shift_score,
            degree_centrality_delta=(
                entity.degree_centrality_delta
            ),
            downstream_reach_ratio_delta=(
                entity.downstream_reach_ratio_delta
            ),
        )
        for entity in shift.entities
    )


def _topology_evidence_v3(
    shift: OrganizationTopologyShift,
) -> tuple[EventTopologyRiskV3Evidence, ...]:
    return tuple(
        EventTopologyRiskV3Evidence(
            entity_id=entity.entity_id,
            shift_score=entity.shift_score,
            degree_centrality_delta=(
                entity.degree_centrality_delta
            ),
            downstream_reach_ratio_delta=(
                entity.downstream_reach_ratio_delta
            ),
        )
        for entity in shift.entities
    )


def _comparison_value(
    result,
) -> RiskComparisonValue:
    return RiskComparisonValue(
        score=result.score,
        semantic_magnitude_contribution=(
            result.semantic_magnitude_contribution
        ),
        legacy_impact_contribution=(
            result.legacy_impact_contribution
        ),
        semantic_severity_contribution=(
            result.semantic_severity_contribution
        ),
        page_context_contribution=(
            result.page_context_contribution
        ),
        confidence_contribution=(
            result.confidence_contribution
        ),
        topology_contribution=(
            result.topology_contribution
        ),
        topology_score=result.topology_score,
        topology_shift_component=(
            result.topology_shift_component
        ),
        topology_centrality_component=(
            result.topology_centrality_component
        ),
        topology_downstream_component=(
            result.topology_downstream_component
        ),
        has_topology_signal=(
            result.has_topology_signal
        ),
        has_previous_topology_state=(
            result.has_previous_topology_state
        ),
    )


def _score_event(
    *,
    event: SemanticDriftEvent,
    shift: OrganizationTopologyShift,
    has_previous_state: bool,
) -> RiskVersionComparison:
    if event.id is None:
        raise ValueError(
            "Semantic drift event must have an id "
            "before Risk V3 requalification."
        )

    if event.organization_id is None:
        raise ValueError(
            "Semantic drift event must belong to an organization."
        )

    organization_id = _as_uuid(
        event.organization_id
    )
    event_id = _as_uuid(event.id)

    normalized = build_risk_v2_inputs(
        event
    )

    evidence_v2 = _topology_evidence_v2(
        shift
    )
    evidence_v3 = _topology_evidence_v3(
        shift
    )

    if not evidence_v2:
        raise ValueError(
            "Risk V3 requalification requires topology "
            "evidence for each eligible event."
        )

    topology_v2 = reduce_event_topology_risk_signal(
        organization_id=organization_id,
        event_id=event_id,
        evidence=evidence_v2,
        has_previous_state=has_previous_state,
    )

    topology_v3 = reduce_event_topology_risk_signal_v3(
        organization_id=organization_id,
        event_id=event_id,
        evidence=evidence_v3,
        has_previous_state=has_previous_state,
    )

    baseline = calculate_risk_score_v2(
        RiskScoreV2Input(
            semantic_magnitude=(
                normalized.semantic_magnitude
            ),
            legacy_impact=normalized.legacy_impact,
            semantic_severity=(
                normalized.semantic_severity
            ),
            page_context=normalized.page_context,
            confidence=normalized.confidence,
            topology=topology_v2.risk_input,
        )
    )

    candidate = calculate_risk_score_v3(
        RiskScoreV3Input(
            semantic_magnitude=(
                normalized.semantic_magnitude
            ),
            legacy_impact=normalized.legacy_impact,
            semantic_severity=(
                normalized.semantic_severity
            ),
            page_context=normalized.page_context,
            confidence=normalized.confidence,
            topology=TopologyRiskV3Input(
                shift_score=topology_v3.shift_score,
                degree_centrality_delta=(
                    topology_v3.degree_centrality_delta
                ),
                downstream_reach_ratio_delta=(
                    topology_v3.downstream_reach_ratio_delta
                ),
                has_previous_state=(
                    topology_v3.has_previous_state
                ),
            ),
        )
    )

    return compare_risk_values(
        organization_id=organization_id,
        event_id=event_id,
        baseline=_comparison_value(baseline),
        candidate=_comparison_value(candidate),
    )


def _eligible_events(
    db: Session,
    *,
    organization_id: uuid.UUID,
) -> tuple[SemanticDriftEvent, ...]:
    return tuple(
        db.query(SemanticDriftEvent)
        .filter(
            SemanticDriftEvent.organization_id
            == organization_id,
            SemanticDriftEvent.analysis_status
            == "completed",
            SemanticDriftEvent.vector_analysis_status
            == "completed",
            SemanticDriftEvent.semantic_magnitude.is_not(
                None
            ),
            SemanticDriftEvent.detected_at.is_not(None),
        )
        .order_by(
            SemanticDriftEvent.detected_at.asc(),
            SemanticDriftEvent.id.asc(),
        )
        .all()
    )


def requalify_risk_v3(
    db: Session,
    *,
    organization_id: uuid.UUID | str,
) -> RiskV3RequalificationResult:
    org_id = _as_uuid(
        organization_id
    )

    with db.no_autoflush:
        events = _eligible_events(
            db,
            organization_id=org_id,
        )

        if not events:
            return RiskV3RequalificationResult(
                organization_id=org_id,
                eligible_event_count=0,
                paired_event_count=0,
                invalid_event_count=0,
                distinct_timestamp_count=0,
                comparisons=(),
                evaluation=None,
                qualification_evidence=(
                    summarize_risk_v3_qualification_evaluation(
                        None
                    )
                ),
            )

        events_by_timestamp: dict[
            datetime,
            list[SemanticDriftEvent],
        ] = {}

        for event in events:
            if event.detected_at is None:
                raise ValueError(
                    "Eligible semantic drift event is missing "
                    "detected_at."
                )

            events_by_timestamp.setdefault(
                event.detected_at,
                [],
            ).append(event)

        graph_cache: dict[
            datetime,
            OrganizationGraphMetrics,
        ] = {}

        previous_metrics = _empty_graph_metrics(
            org_id
        )
        has_previous_state = False
        comparisons: list[
            RiskVersionComparison
        ] = []
        invalid_event_count = 0

        for observed_at in sorted(
            events_by_timestamp
        ):
            current_metrics = (
                calculate_organization_graph_metrics_as_of(
                    db=db,
                    organization_id=org_id,
                    observed_at=observed_at,
                )
            )

            graph_cache[
                observed_at
            ] = current_metrics

            shift = compare_graph_metrics(
                previous=previous_metrics,
                current=current_metrics,
            )

            for event in events_by_timestamp[
                observed_at
            ]:
                try:
                    comparison = _score_event(
                        event=event,
                        shift=shift,
                        has_previous_state=(
                            has_previous_state
                        ),
                    )
                except RiskScoringV2ValidationError:
                    invalid_event_count += 1
                    continue

                comparisons.append(
                    comparison
                )

            previous_metrics = graph_cache[
                observed_at
            ]
            has_previous_state = True

        evaluation = (
            evaluate_risk_shadow_comparisons(
                comparisons
            )
            if comparisons
            else None
        )

        qualification_evidence = (
            summarize_risk_v3_qualification_evaluation(
                evaluation
            )
        )

        return RiskV3RequalificationResult(
            organization_id=org_id,
            eligible_event_count=len(events),
            paired_event_count=len(comparisons),
            invalid_event_count=invalid_event_count,
            distinct_timestamp_count=len(
                graph_cache
            ),
            comparisons=tuple(comparisons),
            evaluation=evaluation,
            qualification_evidence=(
                qualification_evidence
            ),
        )
