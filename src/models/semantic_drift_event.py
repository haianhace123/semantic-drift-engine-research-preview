"""
SQLAlchemy ORM model for SemanticDriftEvent.
Tracks semantic changes between document versions.
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


VECTOR_ANALYSIS_STATUS_PENDING = "pending"
VECTOR_ANALYSIS_STATUS_PROCESSING = "processing"
VECTOR_ANALYSIS_STATUS_COMPLETED = "completed"
VECTOR_ANALYSIS_STATUS_FAILED = "failed"
TEMPORAL_INVALIDATION_STATUS_PENDING = "pending"
TEMPORAL_INVALIDATION_STATUS_COMPLETED = "completed"
TEMPORAL_INVALIDATION_STATUS_FAILED = "failed"
RISK_SCORING_STATUS_PENDING = "pending"
RISK_SCORING_STATUS_PROCESSING = "processing"
RISK_SCORING_STATUS_COMPLETED = "completed"
RISK_SCORING_STATUS_FAILED = "failed"

RISK_SCORE_VERSION_V1 = "v1"


class SemanticDriftEvent(Base):
    __tablename__ = "semantic_drift_events"

    __table_args__ = (
        UniqueConstraint(
            "old_snapshot_id",
            "new_snapshot_id",
            name="uq_semantic_drift_event_snapshot_pair",
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            name="uq_semantic_drift_events_org_id",
        ),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # -----------------------------------------------------------------------
    # Foreign keys
    # -----------------------------------------------------------------------

    monitored_page_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "monitored_pages.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
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

    old_snapshot_id = Column(
        UUID(as_uuid=True),
        ForeignKey("page_snapshots.id"),
        nullable=False,
        index=True,
    )

    new_snapshot_id = Column(
        UUID(as_uuid=True),
        ForeignKey("page_snapshots.id"),
        nullable=False,
        index=True,
    )

    # -----------------------------------------------------------------------
    # Core data
    # -----------------------------------------------------------------------

    semantic_delta_json = Column(
        JSON,
        nullable=False,
    )

    summary_text = Column(
        Text,
        nullable=True,
    )

    # -----------------------------------------------------------------------
    # Analysis state
    #
    # Keep analysis status separate from risk level.
    # A failed AI analysis is still a real drift event.
    # -----------------------------------------------------------------------

    analysis_status = Column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )

    analysis_error = Column(
        Text,
        nullable=True,
    )

    analysis_attempts = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    analyzed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    # -----------------------------------------------------------------------
    # Vector semantic analysis
    #
    # semantic_similarity:
    #     Cosine similarity between old/new snapshot embeddings.
    #
    # semantic_distance:
    #     Raw cosine distance. For normalized cosine semantics this is
    #     normally 1 - similarity.
    #
    # semantic_magnitude:
    #     Application-level normalized magnitude used later by the
    #     deterministic risk scoring engine.
    # -----------------------------------------------------------------------

    semantic_similarity = Column(
        Float,
        nullable=True,
    )

    semantic_distance = Column(
        Float,
        nullable=True,
    )

    semantic_magnitude = Column(
        Float,
        nullable=True,
    )

    vector_analysis_status = Column(
        String(20),
        nullable=False,
        default=VECTOR_ANALYSIS_STATUS_PENDING,
        server_default=VECTOR_ANALYSIS_STATUS_PENDING,
    )

    vector_analysis_error = Column(
        Text,
        nullable=True,
    )

    vector_analysis_attempts = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    vector_analyzed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    # -----------------------------------------------------------------------
    # Risk & confidence
    # -----------------------------------------------------------------------

    risk_level = Column(
        String(20),
        nullable=False,
        default="unknown",
        server_default="unknown",
        index=True,
    )

    confidence = Column(
        Float,
        nullable=True,
    )

    impact_score = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    # -----------------------------------------------------------------------
    # Deterministic risk scoring
    #
    # risk_score is intentionally separate from impact_score.
    #
    # impact_score:
    #     Legacy semantic-analysis impact signal.
    #
    # risk_score:
    #     Versioned deterministic score combining vector magnitude,
    #     legacy impact, semantic severity, monitored-page context,
    #     and confidence.
    # -----------------------------------------------------------------------

    risk_score = Column(
        Float,
        nullable=True,
    )

    risk_score_version = Column(
        String(20),
        nullable=True,
    )

    risk_score_components = Column(
        JSON,
        nullable=True,
    )

    risk_scoring_status = Column(
        String(20),
        nullable=False,
        default=RISK_SCORING_STATUS_PENDING,
        server_default=RISK_SCORING_STATUS_PENDING,
        index=True,
    )

    risk_scoring_error = Column(
        Text,
        nullable=True,
    )

    risk_scoring_attempts = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    risk_scored_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Worker scheduling metadata. This is deliberately separate from the
    # deterministic risk scoring lifecycle above.
    risk_retry_after = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    temporal_invalidation_status = Column(
        String(20),
        nullable=False,
        default=TEMPORAL_INVALIDATION_STATUS_PENDING,
        server_default=TEMPORAL_INVALIDATION_STATUS_PENDING,
    )
    temporal_invalidation_error = Column(
        Text,
        nullable=True,
    )
    temporal_invalidation_attempts = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    temporal_invalidation_analyzed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    # -----------------------------------------------------------------------
    # Classification
    # -----------------------------------------------------------------------

    category = Column(
        String(50),
        nullable=True,
    )  # pricing, legal, policy, feature, security

    change_type = Column(
        String(50),
        nullable=True,
    )  # addition, modification, removal, restructuring

    # -----------------------------------------------------------------------
    # Counts / analysis metadata
    # -----------------------------------------------------------------------

    changes_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    chunk_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    llm_tokens_used = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    # -----------------------------------------------------------------------
    # Relationships / affected entities
    # -----------------------------------------------------------------------

    related_events = Column(
        JSON,
        nullable=False,
        default=list,
        server_default="[]",
    )

    entities_affected = Column(
        JSON,
        nullable=False,
        default=list,
        server_default="[]",
    )

    # -----------------------------------------------------------------------
    # Timestamps
    # -----------------------------------------------------------------------

    detected_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    first_seen_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    last_seen_at = Column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # -----------------------------------------------------------------------
    # Flags
    # -----------------------------------------------------------------------

    is_anomaly = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        index=True,
    )

    is_pattern_part = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    is_reviewed = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        index=True,
    )

    is_archived = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        index=True,
    )

    # -----------------------------------------------------------------------
    # Relationships
    # -----------------------------------------------------------------------

    monitored_page = relationship(
        "MonitoredPage",
        backref="drift_events",
    )

    organization = relationship(
        "Organization",
    )

    old_snapshot = relationship(
        "PageSnapshot",
        foreign_keys=[old_snapshot_id],
    )

    new_snapshot = relationship(
        "PageSnapshot",
        foreign_keys=[new_snapshot_id],
    )

    def __repr__(self) -> str:
        return (
            "<SemanticDriftEvent("
            f"risk={self.risk_level}, "
            f"status={self.analysis_status}, "
            f"vector_status={self.vector_analysis_status}, "
            f"confidence={self.confidence}"
            ")>"
        )