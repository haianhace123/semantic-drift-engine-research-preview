from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


PIPELINE_PATTERN_DETECTION_V1 = "pattern_detection_v1"
PIPELINE_ANOMALY_DETECTION_V1 = "anomaly_detection_v1"
PIPELINE_ALERT_GENERATION_V1 = "alert_generation_v1"
PIPELINE_ALERT_PATTERN_MATERIALIZATION_V1 = (
    "alert_pattern_materialization_v1"
)
PIPELINE_ALERT_ANOMALY_MATERIALIZATION_V1 = (
    "alert_anomaly_materialization_v1"
)
PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1 = (
    "alert_delivery_reconciliation_v1"
)


class PipelineCheckpoint(Base):
    """
    Durable organization-scoped watermark for asynchronous
    intelligence pipeline stages.

    source_created_at:
        Materialization-time watermark of the upstream source.

    source_record_id:
        Stable tie-breaker when multiple upstream records share
        the same source_created_at value.

    knowledge_cursor:
        Temporal cursor used by the deterministic detector.

    processed_at:
        Wall-clock time when the stage most recently completed
        successfully.
    """

    __tablename__ = "pipeline_checkpoints"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "pipeline_name",
            name="uq_pipeline_checkpoints_org_pipeline",
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

    pipeline_name = Column(
        String(64),
        nullable=False,
    )

    source_created_at = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    source_record_id = Column(
        UUID(as_uuid=True),
        nullable=False,
    )

    knowledge_cursor = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    processed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    metadata_json = Column(
        JSONB,
        nullable=True,
    )

    organization = relationship(
        "Organization",
    )

    def __repr__(self) -> str:
        return (
            "<PipelineCheckpoint("
            f"id={self.id}, "
            f"organization_id={self.organization_id}, "
            f"pipeline_name={self.pipeline_name!r}, "
            f"knowledge_cursor={self.knowledge_cursor}"
            ")>"
        )
