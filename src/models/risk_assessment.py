from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"

    __table_args__ = (
        CheckConstraint(
            "score >= 0.0 AND score <= 1.0",
            name="ck_risk_assessment_score",
        ),
        CheckConstraint(
            (
                "semantic_magnitude_contribution >= 0.0 "
                "AND semantic_magnitude_contribution <= 1.0"
            ),
            name="ck_risk_assessment_semantic_magnitude",
        ),
        CheckConstraint(
            (
                "legacy_impact_contribution >= 0.0 "
                "AND legacy_impact_contribution <= 1.0"
            ),
            name="ck_risk_assessment_legacy_impact",
        ),
        CheckConstraint(
            (
                "semantic_severity_contribution >= 0.0 "
                "AND semantic_severity_contribution <= 1.0"
            ),
            name="ck_risk_assessment_semantic_severity",
        ),
        CheckConstraint(
            (
                "page_context_contribution >= 0.0 "
                "AND page_context_contribution <= 1.0"
            ),
            name="ck_risk_assessment_page_context",
        ),
        CheckConstraint(
            (
                "confidence_contribution >= 0.0 "
                "AND confidence_contribution <= 1.0"
            ),
            name="ck_risk_assessment_confidence",
        ),
        CheckConstraint(
            (
                "topology_score >= 0.0 "
                "AND topology_score <= 1.0"
            ),
            name="ck_risk_assessment_topology_score",
        ),
        CheckConstraint(
            (
                "topology_contribution >= 0.0 "
                "AND topology_contribution <= 1.0"
            ),
            name="ck_risk_assessment_topology_contribution",
        ),
        CheckConstraint(
            (
                "topology_shift_component >= 0.0 "
                "AND topology_shift_component <= 1.0"
            ),
            name="ck_risk_assessment_topology_shift",
        ),
        CheckConstraint(
            (
                "topology_centrality_component >= 0.0 "
                "AND topology_centrality_component <= 1.0"
            ),
            name="ck_risk_assessment_topology_centrality",
        ),
        CheckConstraint(
            (
                "topology_downstream_component >= 0.0 "
                "AND topology_downstream_component <= 1.0"
            ),
            name="ck_risk_assessment_topology_downstream",
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

    semantic_drift_event_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "semantic_drift_events.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    risk_version = Column(
        String(32),
        nullable=False,
    )

    assessed_at = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    score = Column(
        Float,
        nullable=False,
    )

    semantic_magnitude_contribution = Column(
        Float,
        nullable=False,
    )

    legacy_impact_contribution = Column(
        Float,
        nullable=False,
    )

    semantic_severity_contribution = Column(
        Float,
        nullable=False,
    )

    page_context_contribution = Column(
        Float,
        nullable=False,
    )

    confidence_contribution = Column(
        Float,
        nullable=False,
    )

    topology_score = Column(
        Float,
        nullable=False,
    )

    topology_contribution = Column(
        Float,
        nullable=False,
    )

    topology_shift_component = Column(
        Float,
        nullable=False,
    )

    topology_centrality_component = Column(
        Float,
        nullable=False,
    )

    topology_downstream_component = Column(
        Float,
        nullable=False,
    )

    has_topology_signal = Column(
        Boolean,
        nullable=False,
    )

    has_previous_topology_state = Column(
        Boolean,
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

    semantic_drift_event = relationship(
        "SemanticDriftEvent",
        foreign_keys=[
            semantic_drift_event_id
        ],
    )

    def __repr__(self) -> str:
        return (
            "<RiskAssessment("
            f"id={self.id}, "
            f"event_id={self.semantic_drift_event_id}, "
            f"version={self.risk_version!r}, "
            f"score={self.score}"
            ")>"
        )