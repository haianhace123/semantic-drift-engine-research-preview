from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


class EntityGraphMetricSnapshot(Base):
    """
    Immutable temporal graph metrics for one entity.

    A snapshot records the structural position of an entity after graph
    evidence associated with a semantic point in time has been applied.

    semantic_drift_event_id is intentionally nullable. Historical graph
    metrics survive event deletion while the event pointer is cleared by
    PostgreSQL ON DELETE SET NULL.
    """

    __tablename__ = (
        "entity_graph_metric_snapshots"
    )

    __table_args__ = (
        CheckConstraint(
            "entity_count >= 0",
            name=(
                "ck_entity_graph_metric_"
                "snapshot_entity_count"
            ),
        ),
        CheckConstraint(
            "active_edge_count >= 0",
            name=(
                "ck_entity_graph_metric_"
                "snapshot_active_edge_count"
            ),
        ),
        CheckConstraint(
            "in_degree >= 0",
            name=(
                "ck_entity_graph_metric_"
                "snapshot_in_degree"
            ),
        ),
        CheckConstraint(
            "out_degree >= 0",
            name=(
                "ck_entity_graph_metric_"
                "snapshot_out_degree"
            ),
        ),
        CheckConstraint(
            "degree >= 0",
            name=(
                "ck_entity_graph_metric_"
                "snapshot_degree"
            ),
        ),
        CheckConstraint(
            "degree = in_degree + out_degree",
            name=(
                "ck_entity_graph_metric_"
                "snapshot_degree_sum"
            ),
        ),
        CheckConstraint(
            (
                "in_degree_centrality >= 0.0 "
                "AND in_degree_centrality <= 1.0"
            ),
            name=(
                "ck_entity_graph_metric_"
                "snapshot_in_centrality"
            ),
        ),
        CheckConstraint(
            (
                "out_degree_centrality >= 0.0 "
                "AND out_degree_centrality <= 1.0"
            ),
            name=(
                "ck_entity_graph_metric_"
                "snapshot_out_centrality"
            ),
        ),
        CheckConstraint(
            (
                "degree_centrality >= 0.0 "
                "AND degree_centrality <= 1.0"
            ),
            name=(
                "ck_entity_graph_metric_"
                "snapshot_degree_centrality"
            ),
        ),
        CheckConstraint(
            "downstream_reach >= 0",
            name=(
                "ck_entity_graph_metric_"
                "snapshot_downstream_reach"
            ),
        ),
        CheckConstraint(
            (
                "downstream_reach_ratio >= 0.0 "
                "AND downstream_reach_ratio <= 1.0"
            ),
            name=(
                "ck_entity_graph_metric_"
                "snapshot_downstream_ratio"
            ),
        ),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    entity_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "entities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    semantic_drift_event_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "semantic_drift_events.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    observed_at = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    entity_count = Column(
        Integer,
        nullable=False,
    )

    active_edge_count = Column(
        Integer,
        nullable=False,
    )

    in_degree = Column(
        Integer,
        nullable=False,
    )

    out_degree = Column(
        Integer,
        nullable=False,
    )

    degree = Column(
        Integer,
        nullable=False,
    )

    in_degree_centrality = Column(
        Float,
        nullable=False,
    )

    out_degree_centrality = Column(
        Float,
        nullable=False,
    )

    degree_centrality = Column(
        Float,
        nullable=False,
    )

    downstream_reach = Column(
        Integer,
        nullable=False,
    )

    downstream_reach_ratio = Column(
        Float,
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    organization = relationship(
        "Organization",
    )

    entity = relationship(
        "Entity",
    )

    semantic_drift_event = relationship(
        "SemanticDriftEvent",
        foreign_keys=[
            semantic_drift_event_id
        ],
    )

    def __repr__(self) -> str:
        return (
            "<EntityGraphMetricSnapshot("
            f"id={self.id}, "
            f"entity_id={self.entity_id}, "
            f"event_id={self.semantic_drift_event_id}, "
            f"degree={self.degree}, "
            f"downstream_reach="
            f"{self.downstream_reach}, "
            f"observed_at={self.observed_at}"
            ")>"
        )