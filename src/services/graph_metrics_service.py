from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from src.models.entity import Entity
from src.models.entity_relationship import EntityRelationship
from src.models.entity_relationship_observation import (
    EntityRelationshipObservation,
)


@dataclass(frozen=True)
class EntityGraphMetrics:
    entity_id: uuid.UUID
    in_degree: int
    out_degree: int
    degree: int
    in_degree_centrality: float
    out_degree_centrality: float
    degree_centrality: float
    downstream_reach: int
    downstream_reach_ratio: float


@dataclass(frozen=True)
class OrganizationGraphMetrics:
    organization_id: uuid.UUID
    entity_count: int
    active_edge_count: int
    entities: tuple[EntityGraphMetrics, ...]


def _as_uuid(
    value: uuid.UUID | str,
) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value

    return uuid.UUID(str(value))


def _bounded_ratio(
    numerator: int,
    denominator: int,
) -> float:
    if denominator <= 0:
        return 0.0

    value = numerator / denominator

    return round(
        max(
            0.0,
            min(1.0, value),
        ),
        6,
    )


def _downstream_reach(
    *,
    source_entity_id: uuid.UUID,
    adjacency: dict[uuid.UUID, set[uuid.UUID]],
) -> int:
    """
    Count unique entities reachable from source through active directed edges.

    Cycles are safe because each entity is visited at most once.
    The source entity itself is never counted as downstream reach.
    """
    visited: set[uuid.UUID] = {
        source_entity_id,
    }

    queue = deque(
        adjacency.get(
            source_entity_id,
            set(),
        )
    )

    while queue:
        entity_id = queue.popleft()

        if entity_id in visited:
            continue

        visited.add(entity_id)

        for target_id in adjacency.get(
            entity_id,
            set(),
        ):
            if target_id not in visited:
                queue.append(target_id)

    return max(
        0,
        len(visited) - 1,
    )


def calculate_graph_metrics(
    *,
    organization_id: uuid.UUID,
    entity_ids: Iterable[uuid.UUID],
    edges: Iterable[
        tuple[uuid.UUID, uuid.UUID]
    ],
) -> OrganizationGraphMetrics:
    """
    Calculate deterministic directed graph metrics.

    Centrality v1 is normalized degree centrality:

        in_degree / (N - 1)
        out_degree / (N - 1)
        (in_degree + out_degree) / (2 * (N - 1))

    Downstream reach is the number of unique entities reachable through
    one or more outgoing edges.

    No database or external service is required by this function.
    """
    normalized_entity_ids = {
        _as_uuid(entity_id)
        for entity_id in entity_ids
    }

    adjacency: dict[
        uuid.UUID,
        set[uuid.UUID],
    ] = defaultdict(set)

    incoming: dict[
        uuid.UUID,
        set[uuid.UUID],
    ] = defaultdict(set)

    normalized_edges: set[
        tuple[uuid.UUID, uuid.UUID]
    ] = set()

    for raw_source_id, raw_target_id in edges:
        source_id = _as_uuid(
            raw_source_id
        )
        target_id = _as_uuid(
            raw_target_id
        )

        if source_id == target_id:
            continue

        if (
            source_id
            not in normalized_entity_ids
            or target_id
            not in normalized_entity_ids
        ):
            continue

        edge = (
            source_id,
            target_id,
        )

        if edge in normalized_edges:
            continue

        normalized_edges.add(edge)

        adjacency[source_id].add(
            target_id
        )

        incoming[target_id].add(
            source_id
        )

    entity_count = len(
        normalized_entity_ids
    )

    degree_denominator = max(
        0,
        entity_count - 1,
    )

    combined_degree_denominator = (
        degree_denominator * 2
    )

    metrics: list[
        EntityGraphMetrics
    ] = []

    for entity_id in sorted(
        normalized_entity_ids,
        key=str,
    ):
        in_degree = len(
            incoming.get(
                entity_id,
                set(),
            )
        )

        out_degree = len(
            adjacency.get(
                entity_id,
                set(),
            )
        )

        degree = (
            in_degree
            + out_degree
        )

        downstream_reach = (
            _downstream_reach(
                source_entity_id=entity_id,
                adjacency=adjacency,
            )
        )

        metrics.append(
            EntityGraphMetrics(
                entity_id=entity_id,
                in_degree=in_degree,
                out_degree=out_degree,
                degree=degree,
                in_degree_centrality=(
                    _bounded_ratio(
                        in_degree,
                        degree_denominator,
                    )
                ),
                out_degree_centrality=(
                    _bounded_ratio(
                        out_degree,
                        degree_denominator,
                    )
                ),
                degree_centrality=(
                    _bounded_ratio(
                        degree,
                        combined_degree_denominator,
                    )
                ),
                downstream_reach=(
                    downstream_reach
                ),
                downstream_reach_ratio=(
                    _bounded_ratio(
                        downstream_reach,
                        degree_denominator,
                    )
                ),
            )
        )

    return OrganizationGraphMetrics(
        organization_id=(
            _as_uuid(
                organization_id
            )
        ),
        entity_count=entity_count,
        active_edge_count=len(
            normalized_edges
        ),
        entities=tuple(metrics),
    )


def calculate_organization_graph_metrics(
    *,
    db: Session,
    organization_id: uuid.UUID | str,
) -> OrganizationGraphMetrics:
    """
    Load the current active tenant graph and calculate metrics.

    Organization isolation is enforced independently on both entities
    and relationships.
    """
    org_id = _as_uuid(
        organization_id
    )

    entity_ids = {
        row[0]
        for row in (
            db.query(Entity.id)
            .filter(
                Entity.organization_id
                == org_id
            )
            .all()
        )
    }

    edge_rows = (
        db.query(
            EntityRelationship.source_entity_id,
            EntityRelationship.target_entity_id,
        )
        .filter(
            EntityRelationship.organization_id
            == org_id,
            EntityRelationship.is_active.is_(
                True
            ),
        )
        .all()
    )

    return calculate_graph_metrics(
        organization_id=org_id,
        entity_ids=entity_ids,
        edges=edge_rows,
    )

def calculate_organization_graph_metrics_as_of(
    *,
    db: Session,
    organization_id: uuid.UUID | str,
    observed_at,
    known_at=None,
) -> OrganizationGraphMetrics:
    """
    Reconstruct the tenant evidence graph at an effective-time cutoff.

    ``observed_at`` is the semantic/effective-time cutoff. An edge exists
    when at least one immutable relationship observation for that directed
    edge is effective at or before that timestamp.

    ``known_at`` is an optional knowledge-time cutoff. When supplied, only
    observations persisted at or before that timestamp are eligible. Omitting
    it reconstructs effective history regardless of when the observation was
    persisted, which is required for event-time topology reconstruction and
    late historical backfill.

    This intentionally uses EntityRelationshipObservation rather than the
    current EntityRelationship.is_active state. Current-state activation is
    not a historical lifecycle log and therefore must not leak future graph
    state into an earlier temporal snapshot.

    Limitation of v1:
    relationship deactivation history is not modeled yet. Therefore this is
    an evidence-existence graph as of the cutoff, not a full replay of edge
    activation/deactivation lifecycle.
    """
    if observed_at is None:
        raise ValueError(
            "observed_at is required for historical graph metrics."
        )

    org_id = _as_uuid(
        organization_id
    )

    entity_ids = {
        row[0]
        for row in (
            db.query(Entity.id)
            .filter(
                Entity.organization_id
                == org_id
            )
            .all()
        )
    }

    observation_filters = [
        EntityRelationshipObservation.organization_id == org_id,
        EntityRelationshipObservation.observed_at <= observed_at,
    ]

    if known_at is not None:
        observation_filters.append(
            EntityRelationshipObservation.created_at <= known_at
        )

    edge_rows = (
        db.query(
            EntityRelationshipObservation.source_entity_id,
            EntityRelationshipObservation.target_entity_id,
        )
        .filter(*observation_filters)
        .distinct()
        .all()
    )

    return calculate_graph_metrics(
        organization_id=org_id,
        entity_ids=entity_ids,
        edges=edge_rows,
    )
