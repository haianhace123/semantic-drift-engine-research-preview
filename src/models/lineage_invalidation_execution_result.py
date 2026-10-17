from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from src.core.database import Base


class LineageInvalidationExecutionResultRecord(Base):
    __tablename__ = "lineage_invalidation_execution_results"

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
    )
    attempt_id = Column(
        UUID(as_uuid=True),
        nullable=False,
    )
    outcome = Column(
        String(16),
        nullable=False,
    )
    recorded_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            (
                "organization_id",
                "attempt_id",
            ),
            (
                "lineage_invalidation_execution_attempts.organization_id",
                "lineage_invalidation_execution_attempts.id",
            ),
            name=(
                "lineage_invalidation_execution_results_"
                "attempt_fkey"
            ),
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "outcome IN ('succeeded', 'failed')",
            name=(
                "ck_lineage_invalidation_execution_"
                "result_outcome"
            ),
        ),
        UniqueConstraint(
            "organization_id",
            "attempt_id",
            name=(
                "uq_lineage_invalidation_execution_"
                "result_attempt"
            ),
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            name=(
                "uq_lineage_invalidation_execution_"
                "results_org_id"
            ),
        ),
        Index(
            (
                "ix_lineage_invalidation_execution_"
                "results_org_recorded_id"
            ),
            "organization_id",
            "recorded_at",
            "id",
        ),
    )