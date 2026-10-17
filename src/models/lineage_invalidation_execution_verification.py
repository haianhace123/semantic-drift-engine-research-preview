from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from src.core.database import Base


class LineageInvalidationExecutionVerificationRecord(Base):
    __tablename__ = "lineage_invalidation_execution_verifications"

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

    execution_result_id = Column(
        UUID(as_uuid=True),
        nullable=False,
    )

    observation_id = Column(
        UUID(as_uuid=True),
        nullable=False,
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
                "execution_result_id",
            ),
            (
                "lineage_invalidation_execution_results.organization_id",
                "lineage_invalidation_execution_results.id",
            ),
            name=(
                "lineage_invalidation_execution_"
                "verifications_result_fkey"
            ),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            (
                "organization_id",
                "observation_id",
            ),
            (
                "lineage_observations.organization_id",
                "lineage_observations.id",
            ),
            name=(
                "lineage_invalidation_execution_"
                "verifications_observation_fkey"
            ),
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "organization_id",
            "execution_result_id",
            name=(
                "uq_lineage_invalidation_execution_"
                "verification_result"
            ),
        ),
        Index(
            (
                "ix_lineage_invalidation_execution_"
                "verifications_org_observation"
            ),
            "organization_id",
            "observation_id",
            "execution_result_id",
        ),
        Index(
            (
                "ix_lineage_invalidation_execution_"
                "verifications_org_created_id"
            ),
            "organization_id",
            "created_at",
            "id",
        ),
    )