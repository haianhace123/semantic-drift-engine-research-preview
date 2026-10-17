from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


class TopologyShiftSnapshot(Base):
    """
    Immutable per-entity topology delta for one semantic point in time.

    Graph metric snapshots remain the source of truth for absolute graph
    metrics. This table stores only the derived temporal delta and its
    deterministic v1 interpretation.

    Event references are nullable because topology history survives event
    deletion through PostgreSQL ON DELETE SET NULL.
    """

    __tablename__ = "topology_shift_snapshots"

    __table_args__ = (
        CheckConstraint(
            (
                "shift_score >= 0.0 "
                "AND shift_score <= 1.0"
            ),
            name="ck_topology_shift_score",
        ),
        CheckConstraint(
            (
                "classification IN ("
                "'stable', "
                "'expanded', "
                "'contracted', "
                "'centrality_gain', "
                "'centrality_loss', "
                "'downstream_gain', "
                "'downstream_loss'"
                ")"
            ),
            name=(
                "ck_topology_shift_"
                "classification"
            ),
        ),
        CheckConstraint(
            (
                "severity IN ("
                "'none', "
                "'low', "
                "'medium', "
                "'high'"
                ")"
            ),
            name="ck_topology_shift_severity",
        ),
        CheckConstraint(
            (
                "previous_observed_at IS NULL "
                "OR previous_observed_at < observed_at"
            ),
            name=(
                "ck_topology_shift_previous_time"
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

    previous_event_id = Column(
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

    previous_observed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    entity_count_delta = Column(
        Integer,
        nullable=False,
    )

    active_edge_count_delta = Column(
        Integer,
        nullable=False,
    )

    in_degree_delta = Column(
        Integer,
        nullable=False,
    )

    out_degree_delta = Column(
        Integer,
        nullable=False,
    )

    degree_delta = Column(
        Integer,
        nullable=False,
    )

    degree_centrality_delta = Column(
        Float,
        nullable=False,
    )

    downstream_reach_delta = Column(
        Integer,
        nullable=False,
    )

    downstream_reach_ratio_delta = Column(
        Float,
        nullable=False,
    )

    classification = Column(
        String(32),
        nullable=False,
    )

    severity = Column(
        String(16),
        nullable=False,
    )

    shift_score = Column(
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

    previous_event = relationship(
        "SemanticDriftEvent",
        foreign_keys=[
            previous_event_id
        ],
    )

    def __repr__(self) -> str:
        return (
            "<TopologyShiftSnapshot("
            f"id={self.id}, "
            f"entity_id={self.entity_id}, "
            f"event_id={self.semantic_drift_event_id}, "
            f"classification={self.classification}, "
            f"severity={self.severity}, "
            f"shift_score={self.shift_score}, "
            f"observed_at={self.observed_at}"
            ")>"
        )