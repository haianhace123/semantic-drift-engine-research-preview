"""
Deterministic explanation engine for What You Missed v1.

This module converts a persisted WhatYouMissedEvidenceBundle into ranked,
structured explanation items.

It intentionally performs:
- no database queries;
- no database writes;
- no LLM/API calls;
- no mutation of evidence.

Risk v2 remains the primary risk authority. Pattern, anomaly, topology and
semantic signals add bounded explanation importance without recomputing risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
import uuid

from src.services.risk_classification_service import (
    classify_risk_score,
    normalize_risk_score,
)
from src.services.what_you_missed_evidence_service import (
    MissedAnomalyEvidence,
    MissedEventEvidence,
    MissedPatternEvidence,
    MissedRiskEvidence,
    MissedTopologyEvidence,
    WhatYouMissedEvidenceBundle,
)


ImportanceLevel = Literal[
    "low",
    "medium",
    "high",
    "critical",
]

ReasonCode = Literal[
    "HIGH_RISK",
    "HIGH_SEMANTIC_MAGNITUDE",
    "HIGH_IMPACT",
    "TOPOLOGY_EXPANSION",
    "TOPOLOGY_CONTRACTION",
    "TOPOLOGY_CENTRALITY_GAIN",
    "TOPOLOGY_CENTRALITY_LOSS",
    "DOWNSTREAM_REACH_GAIN",
    "DOWNSTREAM_REACH_LOSS",
    "REPEATED_DRIFT",
    "RISK_ESCALATION",
    "TOPOLOGY_SEQUENCE",
    "ACTIVITY_BURST",
]


@dataclass(frozen=True)
class ExplanationReason:
    code: ReasonCode
    weight: float
    text: str


@dataclass(frozen=True)
class WhatYouMissedItem:
    event_id: uuid.UUID
    entity_id: uuid.UUID
    monitored_page_id: uuid.UUID

    entity_name: str
    page_url: str
    page_type: str

    occurred_at: datetime

    importance_score: float
    importance_level: ImportanceLevel

    headline: str
    summary: str | None

    risk_score: float | None
    semantic_magnitude: float | None
    impact_score: int

    pattern_ids: tuple[uuid.UUID, ...]
    anomaly_ids: tuple[uuid.UUID, ...]
    topology_shift_ids: tuple[uuid.UUID, ...]

    reasons: tuple[ExplanationReason, ...]


@dataclass(frozen=True)
class WhatYouMissedExplanation:
    organization_id: uuid.UUID
    since: datetime
    until: datetime

    event_count: int
    explained_event_count: int

    items: tuple[WhatYouMissedItem, ...]


def _clamp01(
    value: float,
) -> float:
    return max(
        0.0,
        min(
            1.0,
            float(value),
        ),
    )


def _normalize_risk_score(
    score: float | None,
) -> float:
    """
    Compatibility wrapper around the canonical Risk v2 normalizer.

    Kept private here because existing What You Missed tests and internal
    helpers use this name.
    """
    return normalize_risk_score(
        score
    )


def _importance_level(
    score: float,
) -> ImportanceLevel:
    """
    Compatibility wrapper around canonical Risk v2 classification.
    """
    return classify_risk_score(
        score
    )


def _best_risk(
    risks: tuple[
        MissedRiskEvidence,
        ...
    ],
) -> MissedRiskEvidence | None:
    if not risks:
        return None

    return max(
        risks,
        key=lambda row: (
            _normalize_risk_score(
                row.score
            ),
            row.assessed_at,
            str(
                row.assessment_id
            ),
        ),
    )


def _topology_reason(
    row: MissedTopologyEvidence,
) -> ExplanationReason | None:
    classification = (
        row.classification
        .strip()
        .lower()
    )

    mapping: dict[
        str,
        tuple[
            ReasonCode,
            str,
        ],
    ] = {
        "expanded": (
            "TOPOLOGY_EXPANSION",
            "The entity's relationship topology expanded.",
        ),
        "contracted": (
            "TOPOLOGY_CONTRACTION",
            "The entity's relationship topology contracted.",
        ),
        "centrality_gain": (
            "TOPOLOGY_CENTRALITY_GAIN",
            "The entity became more central in the relationship graph.",
        ),
        "centrality_loss": (
            "TOPOLOGY_CENTRALITY_LOSS",
            "The entity became less central in the relationship graph.",
        ),
        "downstream_gain": (
            "DOWNSTREAM_REACH_GAIN",
            "The entity can reach more downstream entities.",
        ),
        "downstream_loss": (
            "DOWNSTREAM_REACH_LOSS",
            "The entity can reach fewer downstream entities.",
        ),
    }

    resolved = mapping.get(
        classification
    )

    if resolved is None:
        return None

    code, text = resolved

    weight = min(
        0.10,
        0.10
        * _clamp01(
            row.shift_score
        ),
    )

    return ExplanationReason(
        code=code,
        weight=round(
            weight,
            6,
        ),
        text=text,
    )


def _pattern_reason(
    row: MissedPatternEvidence,
) -> ExplanationReason | None:
    pattern_type = (
        row.pattern_type
        .strip()
        .lower()
    )

    mapping: dict[
        str,
        tuple[
            ReasonCode,
            str,
        ],
    ] = {
        "repeated_drift": (
            "REPEATED_DRIFT",
            "This event is part of a repeated drift pattern.",
        ),
        "risk_escalation": (
            "RISK_ESCALATION",
            "This event is part of an escalating risk sequence.",
        ),
        "topology_sequence": (
            "TOPOLOGY_SEQUENCE",
            "This event is part of a repeated topology-shift sequence.",
        ),
    }

    resolved = mapping.get(
        pattern_type
    )

    if resolved is None:
        return None

    code, text = resolved

    weight = min(
        0.10,
        0.10
        * _clamp01(
            row.significance_score
        ),
    )

    return ExplanationReason(
        code=code,
        weight=round(
            weight,
            6,
        ),
        text=text,
    )


def _anomaly_reason(
    row: MissedAnomalyEvidence,
) -> ExplanationReason | None:
    anomaly_type = (
        row.anomaly_type
        .strip()
        .lower()
    )

    if anomaly_type != "activity_burst":
        return None

    weight = min(
        0.10,
        0.10
        * _clamp01(
            row.significance
        ),
    )

    return ExplanationReason(
        code="ACTIVITY_BURST",
        weight=round(
            weight,
            6,
        ),
        text=(
            "Activity exceeded the page's historical baseline."
        ),
    )


def _semantic_reason(
    event: MissedEventEvidence,
) -> ExplanationReason | None:
    if event.semantic_magnitude is None:
        return None

    magnitude = _clamp01(
        event.semantic_magnitude
    )

    if magnitude < 0.70:
        return None

    return ExplanationReason(
        code="HIGH_SEMANTIC_MAGNITUDE",
        weight=round(
            min(
                0.08,
                0.08 * magnitude,
            ),
            6,
        ),
        text=(
            "The semantic meaning changed substantially."
        ),
    )


def _impact_reason(
    event: MissedEventEvidence,
) -> ExplanationReason | None:
    normalized = _clamp01(
        float(
            event.impact_score
        )
        / 100.0
    )

    if normalized < 0.70:
        return None

    return ExplanationReason(
        code="HIGH_IMPACT",
        weight=round(
            min(
                0.07,
                0.07 * normalized,
            ),
            6,
        ),
        text=(
            "The underlying drift event has a high impact score."
        ),
    )


def _risk_reason(
    risk: MissedRiskEvidence | None,
) -> ExplanationReason | None:
    if risk is None:
        return None

    normalized = _normalize_risk_score(
        risk.score
    )

    if normalized < 0.65:
        return None

    return ExplanationReason(
        code="HIGH_RISK",
        weight=0.0,
        text=(
            "Risk v2 classified this change as materially important."
        ),
    )


def _dedupe_reasons(
    reasons: list[
        ExplanationReason
    ],
) -> tuple[
    ExplanationReason,
    ...
]:
    by_code: dict[
        ReasonCode,
        ExplanationReason,
    ] = {}

    for reason in reasons:
        existing = by_code.get(
            reason.code
        )

        if (
            existing is None
            or reason.weight
            > existing.weight
        ):
            by_code[
                reason.code
            ] = reason

    return tuple(
        sorted(
            by_code.values(),
            key=lambda reason: (
                -reason.weight,
                reason.code,
            ),
        )
    )


def _headline(
    event: MissedEventEvidence,
    *,
    risk: MissedRiskEvidence | None,
    patterns: tuple[
        MissedPatternEvidence,
        ...
    ],
    anomalies: tuple[
        MissedAnomalyEvidence,
        ...
    ],
    topology: tuple[
        MissedTopologyEvidence,
        ...
    ],
) -> str:
    if anomalies:
        return (
            f"Unusual activity detected for "
            f"{event.entity_name}"
        )

    if any(
        row.pattern_type
        == "risk_escalation"
        for row in patterns
    ):
        return (
            f"Risk is escalating for "
            f"{event.entity_name}"
        )

    if patterns:
        return (
            f"Repeated drift detected for "
            f"{event.entity_name}"
        )

    if topology:
        return (
            f"Relationship topology changed for "
            f"{event.entity_name}"
        )

    if (
        risk is not None
        and _normalize_risk_score(
            risk.score
        )
        >= 0.65
    ):
        return (
            f"High-risk change detected for "
            f"{event.entity_name}"
        )

    return (
        f"Semantic change detected for "
        f"{event.entity_name}"
    )


def _build_item(
    event: MissedEventEvidence,
    *,
    risks: tuple[MissedRiskEvidence, ...],
    topology: tuple[MissedTopologyEvidence, ...],
    patterns: tuple[MissedPatternEvidence, ...],
    anomalies: tuple[MissedAnomalyEvidence, ...],
) -> WhatYouMissedItem:
    best_risk = _best_risk(risks)

    normalized_risk_score = (
        _normalize_risk_score(best_risk.score)
        if best_risk is not None
        else None
    )

    semantic_base = _clamp01(
        event.semantic_magnitude or 0.0
    )

    impact_base = _clamp01(
        float(event.impact_score or 0) / 100.0
    )

    topology_base = max(
        (
            _clamp01(row.shift_score)
            for row in topology
        ),
        default=0.0,
    )

    pattern_strength = max(
        (
            _clamp01(row.significance_score)
            for row in patterns
        ),
        default=0.0,
    )

    anomaly_strength = max(
        (
            _clamp01(row.significance)
            for row in anomalies
        ),
        default=0.0,
    )

    reasons: list[ExplanationReason] = []

    if (
        normalized_risk_score is not None
        and normalized_risk_score >= 0.65
    ):
        reasons.append(
            ExplanationReason(
                code="HIGH_RISK",
                weight=round(
                    normalized_risk_score,
                    6,
                ),
                text=(
                    "Risk v2 đánh giá thay đổi này "
                    "có mức rủi ro cao."
                ),
            )
        )

    if semantic_base >= 0.70:
        reasons.append(
            ExplanationReason(
                code="HIGH_SEMANTIC_MAGNITUDE",
                weight=round(
                    semantic_base,
                    6,
                ),
                text=(
                    "Nội dung có mức thay đổi ngữ nghĩa "
                    "đáng kể so với trạng thái trước đó."
                ),
            )
        )

    if impact_base >= 0.70:
        reasons.append(
            ExplanationReason(
                code="HIGH_IMPACT",
                weight=round(
                    impact_base,
                    6,
                ),
                text=(
                    "Sự kiện có mức tác động cao theo "
                    "đánh giá của semantic drift pipeline."
                ),
            )
        )

    for row in topology:
        reason = _topology_reason(row)
        if reason is not None:
            reasons.append(reason)

    for row in patterns:
        reason = _pattern_reason(row)
        if reason is not None:
            reasons.append(reason)

    for row in anomalies:
        reason = _anomaly_reason(row)
        if reason is not None:
            reasons.append(reason)

    normalized_reasons = _dedupe_reasons(
        reasons
    )

    # ---------------------------------------------------------
    # Importance scoring contract
    # ---------------------------------------------------------
    #
    # Persisted Risk v2 already incorporates:
    #
    # - semantic magnitude
    # - legacy impact
    # - semantic severity
    # - page context
    # - confidence
    # - topology
    #
    # Therefore those signals remain visible as explanation
    # reasons, but MUST NOT be added to importance_score again
    # when Risk v2 exists.
    #
    # Pattern and anomaly evidence are downstream signals that
    # are not part of Risk v2, so they may provide a small,
    # bounded uplift.
    #
    if normalized_risk_score is not None:
        pattern_uplift = (
            0.05
            * pattern_strength
        )

        anomaly_uplift = (
            0.10
            * anomaly_strength
        )

        importance_score = _clamp01(
            normalized_risk_score
            + pattern_uplift
            + anomaly_uplift
        )

    else:
        # No persisted Risk v2 exists yet.
        #
        # Build a deterministic fallback from primitive evidence:
        #
        #   semantic magnitude : 55%
        #   impact             : 30%
        #   topology           : 15%
        #
        fallback_base = (
            0.55 * semantic_base
            + 0.30 * impact_base
            + 0.15 * topology_base
        )

        pattern_uplift = (
            0.10
            * pattern_strength
        )

        anomaly_uplift = (
            0.15
            * anomaly_strength
        )

        importance_score = _clamp01(
            fallback_base
            + pattern_uplift
            + anomaly_uplift
        )

    importance_score = round(
        importance_score,
        6,
    )

    pattern_ids = tuple(
        sorted(
            (
                row.pattern_id
                for row in patterns
            ),
            key=str,
        )
    )

    anomaly_ids = tuple(
        sorted(
            (
                row.anomaly_id
                for row in anomalies
            ),
            key=str,
        )
    )

    topology_shift_ids = tuple(
        sorted(
            (
                row.topology_shift_id
                for row in topology
            ),
            key=str,
        )
    )

    return WhatYouMissedItem(
        event_id=event.event_id,
        entity_id=event.entity_id,
        monitored_page_id=(
            event.monitored_page_id
        ),
        entity_name=event.entity_name,
        page_url=event.page_url,
        page_type=event.page_type,
        occurred_at=event.occurred_at,
        importance_score=importance_score,
        importance_level=_importance_level(
            importance_score
        ),
        headline=_headline(
            event,
            risk=best_risk,
            patterns=patterns,
            anomalies=anomalies,
            topology=topology,
        ),
        summary=event.summary_text,
        risk_score=(
            round(
                normalized_risk_score,
                6,
            )
            if normalized_risk_score
            is not None
            else None
        ),
        semantic_magnitude=(
            event.semantic_magnitude
        ),
        impact_score=event.impact_score,
        pattern_ids=pattern_ids,
        anomaly_ids=anomaly_ids,
        topology_shift_ids=(
            topology_shift_ids
        ),
        reasons=normalized_reasons,
    )


def explain_what_you_missed(
    evidence: WhatYouMissedEvidenceBundle,
    *,
    limit: int | None = None,
) -> WhatYouMissedExplanation:
    """
    Convert persisted evidence into deterministic ranked explanation items.

    Ranking:
        1. importance_score descending
        2. occurred_at descending
        3. event UUID ascending

    ``limit`` only affects returned items. ``event_count`` always describes
    the complete evidence window.
    """

    if not isinstance(
        evidence,
        WhatYouMissedEvidenceBundle,
    ):
        raise TypeError(
            "evidence must be a WhatYouMissedEvidenceBundle."
        )

    if (
        limit is not None
        and (
            isinstance(
                limit,
                bool,
            )
            or not isinstance(
                limit,
                int,
            )
            or limit < 1
        )
    ):
        raise ValueError(
            "limit must be a positive integer or None."
        )

    risks_by_event: dict[
        uuid.UUID,
        list[
            MissedRiskEvidence
        ],
    ] = {}

    for row in evidence.risks:
        risks_by_event.setdefault(
            row.event_id,
            [],
        ).append(
            row
        )

    topology_by_event: dict[
        uuid.UUID,
        list[
            MissedTopologyEvidence
        ],
    ] = {}

    for row in evidence.topology:
        topology_by_event.setdefault(
            row.event_id,
            [],
        ).append(
            row
        )

    patterns_by_event: dict[
        uuid.UUID,
        list[
            MissedPatternEvidence
        ],
    ] = {}

    for row in evidence.patterns:
        for event_id in (
            row.matched_event_ids
        ):
            patterns_by_event.setdefault(
                event_id,
                [],
            ).append(
                row
            )

    anomalies_by_event: dict[
        uuid.UUID,
        list[
            MissedAnomalyEvidence
        ],
    ] = {}

    for row in evidence.anomalies:
        anomalies_by_event.setdefault(
            row.event_id,
            [],
        ).append(
            row
        )

    items = [
        _build_item(
            event,
            risks=tuple(
                risks_by_event.get(
                    event.event_id,
                    [],
                )
            ),
            topology=tuple(
                topology_by_event.get(
                    event.event_id,
                    [],
                )
            ),
            patterns=tuple(
                patterns_by_event.get(
                    event.event_id,
                    [],
                )
            ),
            anomalies=tuple(
                anomalies_by_event.get(
                    event.event_id,
                    [],
                )
            ),
        )
        for event in evidence.events
    ]

    items.sort(
        key=lambda item: (
            -item.importance_score,
            -item.occurred_at.timestamp(),
            str(
                item.event_id
            ),
        )
    )

    if limit is not None:
        items = items[
            :limit
        ]

    return WhatYouMissedExplanation(
        organization_id=(
            evidence.organization_id
        ),
        since=evidence.since,
        until=evidence.until,
        event_count=len(
            evidence.events
        ),
        explained_event_count=len(
            items
        ),
        items=tuple(
            items
        ),
    )