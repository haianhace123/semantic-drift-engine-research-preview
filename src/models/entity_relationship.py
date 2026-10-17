from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


RELATIONSHIP_TYPE_MENTIONED = "mentioned"

EVIDENCE_METHOD_DETERMINISTIC_V1 = (
    "deterministic_name_domain_v1"
)


class EntityRelationship(Base):
    """
    Directed relationship edge between two entities.

    The row represents the current accumulated state of an edge.
    Temporal provenance is retained through first/last event and
    first/last seen timestamps.

    Event-level history remains available through
    SemanticDriftEvent.entities_affected and the originating events.
    """

    __tablename__ = "entity_relationships"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "source_entity_id",
            "target_entity_id",
            "relationship_type",
            name="uq_entity_relationship_edge",
        ),
        CheckConstraint(
            "source_entity_id <> target_entity_id",
            name="ck_entity_relationship_no_self_edge",
        ),
        CheckConstraint(
            "evidence_count >= 1",
            name="ck_entity_relationship_evidence_count",
        ),
        CheckConstraint(
            "strength >= 0.0 AND strength <= 1.0",
            name="ck_entity_relationship_strength",
        ),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_entity_relationship_confidence",
        ),
        CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="ck_entity_relationship_time_order",
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

    first_event_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "semantic_drift_events.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    last_event_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "semantic_drift_events.id",
            ondelete="SET NULL",
        ),
        nullable=True,
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

    evidence_count = Column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    strength = Column(
        Float,
        nullable=False,
        default=1.0,
        server_default="1.0",
    )

    confidence = Column(
        Float,
        nullable=False,
        default=1.0,
        server_default="1.0",
    )

    first_seen_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    last_seen_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    organization = relationship(
        "Organization",
    )

    source_entity = relationship(
        "Entity",
        foreign_keys=[source_entity_id],
    )

    target_entity = relationship(
        "Entity",
        foreign_keys=[target_entity_id],
    )

    first_event = relationship(
        "SemanticDriftEvent",
        foreign_keys=[first_event_id],
    )

    last_event = relationship(
        "SemanticDriftEvent",
        foreign_keys=[last_event_id],
    )

    def __repr__(self) -> str:
        return (
            "<EntityRelationship("
            f"id={self.id}, "
            f"organization_id={self.organization_id}, "
            f"source_entity_id={self.source_entity_id}, "
            f"target_entity_id={self.target_entity_id}, "
            f"type={self.relationship_type!r}, "
            f"evidence_count={self.evidence_count}, "
            f"is_active={self.is_active}"
            ")>"
        )