from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base
from src.models.entity_relationship import (
    EVIDENCE_METHOD_DETERMINISTIC_V1,
    RELATIONSHIP_TYPE_MENTIONED,
)


class EntityRelationshipObservation(Base):
    """
    Immutable event-level observation of an entity relationship.

    One row means that one SemanticDriftEvent provided evidence for
    one directed graph edge at one semantic point in time.

    The unique relationship/event constraint makes graph persistence
    idempotent when the same event is retried.
    """

    __tablename__ = "entity_relationship_observations"

    __table_args__ = (
        UniqueConstraint(
            "relationship_id",
            "semantic_drift_event_id",
            name="uq_entity_relationship_observation_event",
        ),
        CheckConstraint(
            "source_entity_id <> target_entity_id",
            name="ck_entity_relationship_observation_no_self_edge",
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

    relationship_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "entity_relationships.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    semantic_drift_event_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "semantic_drift_events.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    source_entity_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "entities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    target_entity_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "entities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    relationship_type = Column(
        String(50),
        nullable=False,
        default=RELATIONSHIP_TYPE_MENTIONED,
        server_default=RELATIONSHIP_TYPE_MENTIONED,
    )

    evidence_method = Column(
        String(100),
        nullable=False,
        default=EVIDENCE_METHOD_DETERMINISTIC_V1,
        server_default=EVIDENCE_METHOD_DETERMINISTIC_V1,
    )

    observed_at = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    organization = relationship("Organization")

    relationship_edge = relationship(
        "EntityRelationship",
        foreign_keys=[relationship_id],
    )

    semantic_drift_event = relationship(
        "SemanticDriftEvent",
        foreign_keys=[semantic_drift_event_id],
    )

    source_entity = relationship(
        "Entity",
        foreign_keys=[source_entity_id],
    )

    target_entity = relationship(
        "Entity",
        foreign_keys=[target_entity_id],
    )

    def __repr__(self) -> str:
        return (
            "<EntityRelationshipObservation("
            f"id={self.id}, "
            f"relationship_id={self.relationship_id}, "
            f"event_id={self.semantic_drift_event_id}, "
            f"observed_at={self.observed_at}"
            ")>"
        )