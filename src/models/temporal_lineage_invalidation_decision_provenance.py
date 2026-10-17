from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from src.core.database import Base


class TemporalLineageInvalidationDecisionProvenanceRecord(Base):
    __tablename__ = (
        "temporal_lineage_invalidation_decision_provenance"
    )

    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    semantic_drift_event_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
    )

    decision_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            (
                "organization_id",
                "semantic_drift_event_id",
            ),
            (
                "semantic_drift_events.organization_id",
                "semantic_drift_events.id",
            ),
            name=(
                "fk_temporal_lineage_invalidation_"
                "provenance_event"
            ),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            (
                "organization_id",
                "decision_id",
            ),
            (
                "lineage_invalidation_decisions.organization_id",
                "lineage_invalidation_decisions.id",
            ),
            name=(
                "fk_temporal_lineage_invalidation_"
                "provenance_decision"
            ),
            ondelete="CASCADE",
        ),
        Index(
            "ix_temporal_lineage_invalidation_provenance_org_event",
            "organization_id",
            "semantic_drift_event_id",
            "decision_id",
        ),
        Index(
            "ix_temporal_lineage_invalidation_provenance_org_decision",
            "organization_id",
            "decision_id",
            "semantic_drift_event_id",
        ),
    )