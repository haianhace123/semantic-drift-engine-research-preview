from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from src.services.graph_metrics_service import (
    EntityGraphMetrics,
    OrganizationGraphMetrics,
)


TopologyShiftClassification = Literal[
    "stable",
    "expanded",
    "contracted",
    "centrality_gain",
    "centrality_loss",
    "downstream_gain",
    "downstream_loss",
]

TopologyShiftSeverity = Literal[
    "none",
    "low",
    "medium",
    "high",
]


@dataclass(frozen=True)
class EntityTopologyShift:
    entity_id: uuid.UUID

    previous_in_degree: int
    current_in_degree: int
    in_degree_delta: int

    previous_out_degree: int
    current_out_degree: int
    out_degree_delta: int

    previous_degree: int
    current_degree: int
    degree_delta: int

    previous_degree_centrality: float
    current_degree_centrality: float
    degree_centrality_delta: float

    previous_downstream_reach: int
    current_downstream_reach: int
    downstream_reach_delta: int

    previous_downstream_reach_ratio: float
    current_downstream_reach_ratio: float
    downstream_reach_ratio_delta: float

    classification: TopologyShiftClassification
    severity: TopologyShiftSeverity
    shift_score: float


@dataclass(frozen=True)
class OrganizationTopologyShift:
    organization_id: uuid.UUID

    previous_entity_count: int
    current_entity_count: int
    entity_count_delta: int

    previous_active_edge_count: int
    current_active_edge_count: int
    active_edge_count_delta: int

    changed_entity_count: int
    max_shift_score: float

    entities: tuple[EntityTopologyShift, ...]


def _rounded_delta(
    current: float,
    previous: float,
) -> float:
    return round(
        current - previous,
        6,
    )


def _bounded_score(
    value: float,
) -> float:
    return round(
        max(
            0.0,
            min(1.0, value),
        ),
        6,
    )


def _classify_shift(
    *,
    degree_delta: int,
    degree_centrality_delta: float,
    downstream_reach_delta: int,
    downstream_reach_ratio_delta: float,
) -> TopologyShiftClassification:
    """
    Return one deterministic primary topology classification.

    Precedence intentionally favors downstream structural impact,
    then centrality movement, then local degree movement.

    This keeps v1 auditable and avoids assigning multiple competing
    primary labels to the same entity.
    """
    if downstream_reach_delta > 0:
        return "downstream_gain"

    if downstream_reach_delta < 0:
        return "downstream_loss"

    if degree_centrality_delta > 0:
        return "centrality_gain"

    if degree_centrality_delta < 0:
        return "centrality_loss"

    if degree_delta > 0:
        return "expanded"

    if degree_delta < 0:
        return "contracted"

    if downstream_reach_ratio_delta > 0:
        return "downstream_gain"

    if downstream_reach_ratio_delta < 0:
        return "downstream_loss"

    return "stable"


def _calculate_shift_score(
    *,
    degree_delta: int,
    degree_centrality_delta: float,
    downstream_reach_delta: int,
    downstream_reach_ratio_delta: float,
    entity_denominator: int,
) -> float:
    """
    Calculate deterministic topology shift magnitude in [0, 1].

    v1 weights:
    - degree centrality movement:       40%
    - downstream reach ratio movement: 40%
    - normalized local degree movement:20%

    Absolute deltas are used because this score measures magnitude.
    Direction is represented separately by classification and raw deltas.

    downstream_reach_delta is accepted intentionally as part of the
    structural input contract, while its normalized ratio is used for
    scoring so organizations of different sizes remain comparable.
    """
    del downstream_reach_delta

    normalized_degree_delta = 0.0

    if entity_denominator > 0:
        normalized_degree_delta = min(
            1.0,
            abs(degree_delta)
            / entity_denominator,
        )

    score = (
        abs(degree_centrality_delta) * 0.40
        + abs(downstream_reach_ratio_delta) * 0.40
        + normalized_degree_delta * 0.20
    )

    return _bounded_score(score)


def _severity_from_score(
    score: float,
) -> TopologyShiftSeverity:
    """
    Map deterministic shift magnitude to a simple v1 severity band.

    Thresholds:
        0          -> none
        < 0.20     -> low
        < 0.50     -> medium
        >= 0.50    -> high
    """
    if score <= 0.0:
        return "none"

    if score < 0.20:
        return "low"

    if score < 0.50:
        return "medium"

    return "high"


def _zero_metrics(
    entity_id: uuid.UUID,
) -> EntityGraphMetrics:
    return EntityGraphMetrics(
        entity_id=entity_id,
        in_degree=0,
        out_degree=0,
        degree=0,
        in_degree_centrality=0.0,
        out_degree_centrality=0.0,
        degree_centrality=0.0,
        downstream_reach=0,
        downstream_reach_ratio=0.0,
    )


def compare_graph_metrics(
    *,
    previous: OrganizationGraphMetrics,
    current: OrganizationGraphMetrics,
) -> OrganizationTopologyShift:
    """
    Compare two deterministic graph metric states.

    Both states must belong to the same organization.

    Entity membership is compared by union. An entity that exists only in
    one side receives zero metrics on the missing side. This makes the pure
    comparison function safe for future temporal replay where entity
    populations may differ between snapshots.

    No database or external service is used.
    """
    if (
        previous.organization_id
        != current.organization_id
    ):
        raise ValueError(
            "Cannot compare graph metrics from "
            "different organizations."
        )

    previous_by_entity = {
        metrics.entity_id: metrics
        for metrics in previous.entities
    }

    current_by_entity = {
        metrics.entity_id: metrics
        for metrics in current.entities
    }

    entity_ids = (
        set(previous_by_entity)
        | set(current_by_entity)
    )

    entity_denominator = max(
        0,
        max(
            previous.entity_count,
            current.entity_count,
        )
        - 1,
    )

    shifts: list[EntityTopologyShift] = []

    for entity_id in sorted(
        entity_ids,
        key=str,
    ):
        previous_metrics = (
            previous_by_entity.get(
                entity_id,
                _zero_metrics(entity_id),
            )
        )

        current_metrics = (
            current_by_entity.get(
                entity_id,
                _zero_metrics(entity_id),
            )
        )

        in_degree_delta = (
            current_metrics.in_degree
            - previous_metrics.in_degree
        )

        out_degree_delta = (
            current_metrics.out_degree
            - previous_metrics.out_degree
        )

        degree_delta = (
            current_metrics.degree
            - previous_metrics.degree
        )

        degree_centrality_delta = (
            _rounded_delta(
                current_metrics.degree_centrality,
                previous_metrics.degree_centrality,
            )
        )

        downstream_reach_delta = (
            current_metrics.downstream_reach
            - previous_metrics.downstream_reach
        )

        downstream_reach_ratio_delta = (
            _rounded_delta(
                current_metrics.downstream_reach_ratio,
                previous_metrics.downstream_reach_ratio,
            )
        )

        classification = _classify_shift(
            degree_delta=degree_delta,
            degree_centrality_delta=(
                degree_centrality_delta
            ),
            downstream_reach_delta=(
                downstream_reach_delta
            ),
            downstream_reach_ratio_delta=(
                downstream_reach_ratio_delta
            ),
        )

        shift_score = _calculate_shift_score(
            degree_delta=degree_delta,
            degree_centrality_delta=(
                degree_centrality_delta
            ),
            downstream_reach_delta=(
                downstream_reach_delta
            ),
            downstream_reach_ratio_delta=(
                downstream_reach_ratio_delta
            ),
            entity_denominator=(
                entity_denominator
            ),
        )

        shifts.append(
            EntityTopologyShift(
                entity_id=entity_id,
                previous_in_degree=(
                    previous_metrics.in_degree
                ),
                current_in_degree=(
                    current_metrics.in_degree
                ),
                in_degree_delta=in_degree_delta,
                previous_out_degree=(
                    previous_metrics.out_degree
                ),
                current_out_degree=(
                    current_metrics.out_degree
                ),
                out_degree_delta=out_degree_delta,
                previous_degree=(
                    previous_metrics.degree
                ),
                current_degree=(
                    current_metrics.degree
                ),
                degree_delta=degree_delta,
                previous_degree_centrality=(
                    previous_metrics.degree_centrality
                ),
                current_degree_centrality=(
                    current_metrics.degree_centrality
                ),
                degree_centrality_delta=(
                    degree_centrality_delta
                ),
                previous_downstream_reach=(
                    previous_metrics.downstream_reach
                ),
                current_downstream_reach=(
                    current_metrics.downstream_reach
                ),
                downstream_reach_delta=(
                    downstream_reach_delta
                ),
                previous_downstream_reach_ratio=(
                    previous_metrics.downstream_reach_ratio
                ),
                current_downstream_reach_ratio=(
                    current_metrics.downstream_reach_ratio
                ),
                downstream_reach_ratio_delta=(
                    downstream_reach_ratio_delta
                ),
                classification=classification,
                severity=(
                    _severity_from_score(
                        shift_score
                    )
                ),
                shift_score=shift_score,
            )
        )

    changed_entity_count = sum(
        1
        for shift in shifts
        if shift.classification != "stable"
    )

    max_shift_score = max(
        (
            shift.shift_score
            for shift in shifts
        ),
        default=0.0,
    )

    return OrganizationTopologyShift(
        organization_id=(
            current.organization_id
        ),
        previous_entity_count=(
            previous.entity_count
        ),
        current_entity_count=(
            current.entity_count
        ),
        entity_count_delta=(
            current.entity_count
            - previous.entity_count
        ),
        previous_active_edge_count=(
            previous.active_edge_count
        ),
        current_active_edge_count=(
            current.active_edge_count
        ),
        active_edge_count_delta=(
            current.active_edge_count
            - previous.active_edge_count
        ),
        changed_entity_count=(
            changed_entity_count
        ),
        max_shift_score=(
            max_shift_score
        ),
        entities=tuple(shifts),
    )