"""
Deterministic pattern detection primitives.

This module contains no database access and no API logic.
It operates only on normalized, persisted temporal evidence supplied
by callers.

Pattern Detection v1 intentionally favors deterministic,
explainable rules over statistical or ML-based discovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
import uuid


PatternKind = Literal[
    "repeated_drift",
    "risk_escalation",
    "topology_sequence",
]


@dataclass(frozen=True)
class PatternEventInput:
    event_id: uuid.UUID
    entity_id: uuid.UUID
    occurred_at: datetime


@dataclass(frozen=True)
class RiskAssessmentInput:
    event_id: uuid.UUID
    entity_id: uuid.UUID
    occurred_at: datetime
    score: float


@dataclass(frozen=True)
class TopologyShiftInput:
    event_id: uuid.UUID
    entity_id: uuid.UUID
    observed_at: datetime
    classification: str
    shift_score: float


@dataclass(frozen=True)
class DetectedPattern:
    pattern_type: PatternKind
    entity_id: uuid.UUID
    event_ids: tuple[uuid.UUID, ...]
    first_event_at: datetime
    last_event_at: datetime
    event_count: int
    significance_score: float
    detection_method: str


def _clamp01(
    value: float,
) -> float:
    return round(
        max(
            0.0,
            min(
                1.0,
                float(value),
            ),
        ),
        6,
    )


def _ordered_events(
    events: list[PatternEventInput],
) -> list[PatternEventInput]:
    return sorted(
        events,
        key=lambda item: (
            item.occurred_at,
            str(item.event_id),
        ),
    )


def detect_repeated_drift_patterns(
    events: list[PatternEventInput],
    *,
    minimum_events: int = 3,
) -> tuple[DetectedPattern, ...]:
    """
    Detect repeated semantic drift for the same entity.

    One v1 pattern is emitted per entity when at least
    ``minimum_events`` events are present in the supplied window.
    """
    if minimum_events < 2:
        raise ValueError(
            "minimum_events must be >= 2"
        )

    grouped: dict[
        uuid.UUID,
        list[PatternEventInput],
    ] = {}

    for event in events:
        grouped.setdefault(
            event.entity_id,
            [],
        ).append(
            event
        )

    patterns: list[DetectedPattern] = []

    for entity_id in sorted(
        grouped,
        key=str,
    ):
        ordered = _ordered_events(
            grouped[entity_id]
        )

        if len(ordered) < minimum_events:
            continue

        count = len(
            ordered
        )

        significance = _clamp01(
            0.50
            + min(
                0.45,
                (
                    count
                    - minimum_events
                    + 1
                )
                * 0.075,
            )
        )

        patterns.append(
            DetectedPattern(
                pattern_type=(
                    "repeated_drift"
                ),
                entity_id=entity_id,
                event_ids=tuple(
                    item.event_id
                    for item in ordered
                ),
                first_event_at=(
                    ordered[0].occurred_at
                ),
                last_event_at=(
                    ordered[-1].occurred_at
                ),
                event_count=count,
                significance_score=(
                    significance
                ),
                detection_method=(
                    "repeated_drift_v1"
                ),
            )
        )

    return tuple(
        patterns
    )


def detect_risk_escalation_patterns(
    assessments: list[
        RiskAssessmentInput
    ],
    *,
    minimum_events: int = 3,
    minimum_step: float = 0.05,
) -> tuple[DetectedPattern, ...]:
    """
    Detect monotonically escalating Risk v2 sequences.

    A sequence requires consecutive assessments for the same
    entity where every score increases by at least minimum_step.
    Only maximal qualifying sequences are emitted.
    """
    if minimum_events < 2:
        raise ValueError(
            "minimum_events must be >= 2"
        )

    if minimum_step < 0:
        raise ValueError(
            "minimum_step must be >= 0"
        )

    grouped: dict[
        uuid.UUID,
        list[RiskAssessmentInput],
    ] = {}

    for assessment in assessments:
        grouped.setdefault(
            assessment.entity_id,
            [],
        ).append(
            assessment
        )

    patterns: list[DetectedPattern] = []

    for entity_id in sorted(
        grouped,
        key=str,
    ):
        ordered = sorted(
            grouped[entity_id],
            key=lambda item: (
                item.occurred_at,
                str(item.event_id),
            ),
        )

        current: list[
            RiskAssessmentInput
        ] = []

        sequences: list[
            list[RiskAssessmentInput]
        ] = []

        for item in ordered:
            if not current:
                current = [
                    item
                ]
                continue

            previous = current[-1]

            if (
                float(item.score)
                - float(previous.score)
                >= minimum_step
            ):
                current.append(
                    item
                )
                continue

            if (
                len(current)
                >= minimum_events
            ):
                sequences.append(
                    current
                )

            current = [
                item
            ]

        if (
            len(current)
            >= minimum_events
        ):
            sequences.append(
                current
            )

        for sequence in sequences:
            total_gain = (
                float(
                    sequence[-1].score
                )
                - float(
                    sequence[0].score
                )
            )

            significance = _clamp01(
                0.55
                + min(
                    0.30,
                    total_gain * 0.50,
                )
                + min(
                    0.15,
                    (
                        len(sequence)
                        - minimum_events
                        + 1
                    )
                    * 0.05,
                )
            )

            patterns.append(
                DetectedPattern(
                    pattern_type=(
                        "risk_escalation"
                    ),
                    entity_id=entity_id,
                    event_ids=tuple(
                        item.event_id
                        for item in sequence
                    ),
                    first_event_at=(
                        sequence[
                            0
                        ].occurred_at
                    ),
                    last_event_at=(
                        sequence[
                            -1
                        ].occurred_at
                    ),
                    event_count=len(
                        sequence
                    ),
                    significance_score=(
                        significance
                    ),
                    detection_method=(
                        "risk_escalation_v1"
                    ),
                )
            )

    patterns.sort(
        key=lambda pattern: (
            pattern.first_event_at,
            str(
                pattern.entity_id
            ),
            pattern.pattern_type,
            tuple(
                str(event_id)
                for event_id
                in pattern.event_ids
            ),
        )
    )

    return tuple(
        patterns
    )


def _topology_direction(
    classification: str,
) -> str | None:
    normalized = str(
        classification
        or ""
    ).strip().lower()

    gains = {
        "expanded",
        "centrality_gain",
        "downstream_gain",
    }

    losses = {
        "contracted",
        "centrality_loss",
        "downstream_loss",
    }

    if normalized in gains:
        return "gain"

    if normalized in losses:
        return "loss"

    return None


def detect_topology_sequence_patterns(
    shifts: list[
        TopologyShiftInput
    ],
    *,
    minimum_events: int = 2,
) -> tuple[DetectedPattern, ...]:
    """
    Detect repeated topology movement in the same direction.

    Stable shifts break a sequence. Gain and loss classifications
    are normalized into directional families.
    """
    if minimum_events < 2:
        raise ValueError(
            "minimum_events must be >= 2"
        )

    grouped: dict[
        uuid.UUID,
        list[TopologyShiftInput],
    ] = {}

    for shift in shifts:
        grouped.setdefault(
            shift.entity_id,
            [],
        ).append(
            shift
        )

    patterns: list[DetectedPattern] = []

    for entity_id in sorted(
        grouped,
        key=str,
    ):
        ordered = sorted(
            grouped[entity_id],
            key=lambda item: (
                item.observed_at,
                str(item.event_id),
            ),
        )

        current: list[
            TopologyShiftInput
        ] = []

        current_direction: (
            str | None
        ) = None

        sequences: list[
            list[TopologyShiftInput]
        ] = []

        for item in ordered:
            direction = (
                _topology_direction(
                    item.classification
                )
            )

            if direction is None:
                if (
                    len(current)
                    >= minimum_events
                ):
                    sequences.append(
                        current
                    )

                current = []
                current_direction = None
                continue

            if (
                not current
                or direction
                == current_direction
            ):
                current.append(
                    item
                )
                current_direction = (
                    direction
                )
                continue

            if (
                len(current)
                >= minimum_events
            ):
                sequences.append(
                    current
                )

            current = [
                item
            ]

            current_direction = (
                direction
            )

        if (
            len(current)
            >= minimum_events
        ):
            sequences.append(
                current
            )

        for sequence in sequences:
            max_shift = max(
                _clamp01(
                    item.shift_score
                )
                for item in sequence
            )

            significance = _clamp01(
                0.50
                + max_shift * 0.35
                + min(
                    0.15,
                    (
                        len(sequence)
                        - minimum_events
                        + 1
                    )
                    * 0.05,
                )
            )

            patterns.append(
                DetectedPattern(
                    pattern_type=(
                        "topology_sequence"
                    ),
                    entity_id=entity_id,
                    event_ids=tuple(
                        item.event_id
                        for item in sequence
                    ),
                    first_event_at=(
                        sequence[
                            0
                        ].observed_at
                    ),
                    last_event_at=(
                        sequence[
                            -1
                        ].observed_at
                    ),
                    event_count=len(
                        sequence
                    ),
                    significance_score=(
                        significance
                    ),
                    detection_method=(
                        "topology_sequence_v1"
                    ),
                )
            )

    patterns.sort(
        key=lambda pattern: (
            pattern.first_event_at,
            str(
                pattern.entity_id
            ),
            pattern.pattern_type,
            tuple(
                str(event_id)
                for event_id
                in pattern.event_ids
            ),
        )
    )

    return tuple(
        patterns
    )