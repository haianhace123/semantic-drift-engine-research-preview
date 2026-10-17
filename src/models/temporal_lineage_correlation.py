from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from src.core.database import Base


class TemporalLineageCorrelationRecord(Base):
    __tablename__ = "temporal_lineage_correlations"

    page_snapshot_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "page_snapshots.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    lineage_observation_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "lineage_observations.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_temporal_lineage_correlations_observation_snapshot",
            "lineage_observation_id",
            "page_snapshot_id",
        ),
    )