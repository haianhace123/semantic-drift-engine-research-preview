from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable

from src.services.graph_metrics_service import (
    calculate_graph_metrics,
)
from src.services.topology_shift_service import (
    OrganizationTopologyShift,
    compare_graph_metrics,
)


@dataclass(frozen=True)
class GraphState:
    entity_ids: tuple[uuid.UUID, ...]
    edges: tuple[tuple[uuid.UUID, uuid.UUID], ...]


@dataclass(frozen=True)
class TopologyPairScore:
    score: float
    shift: OrganizationTopologyShift


class ProductionTopologyAdapter:
    """
    Score structural change using the production graph pipeline.

    Benchmark fixtures describe only graph state. Derived graph metrics
    and topology shift values are always calculated by production code.
    """

    def score(
        self,
        *,
        organization_id: uuid.UUID,
        previous: GraphState,
        current: GraphState,
    ) -> TopologyPairScore:
        previous_metrics = calculate_graph_metrics(
            organization_id=organization_id,
            entity_ids=previous.entity_ids,
            edges=previous.edges,
        )
        current_metrics = calculate_graph_metrics(
            organization_id=organization_id,
            entity_ids=current.entity_ids,
            edges=current.edges,
        )

        shift = compare_graph_metrics(
            previous=previous_metrics,
            current=current_metrics,
        )

        return TopologyPairScore(
            score=shift.max_shift_score,
            shift=shift,
        )


def graph_state(
    *,
    entity_ids: Iterable[uuid.UUID],
    edges: Iterable[tuple[uuid.UUID, uuid.UUID]],
) -> GraphState:
    return GraphState(
        entity_ids=tuple(entity_ids),
        edges=tuple(edges),
    )