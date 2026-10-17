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


class LineageInvalidationExecutionIntentRecord(Base):
    __tablename__ = "lineage_invalidation_execution_intents"

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
    decision_id = Column(
        UUID(as_uuid=True),
        nullable=False,
    )
    authorized_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
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
                "lineage_invalidation_execution_intents_"
                "decision_fkey"
            ),
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "organization_id",
            "decision_id",
            name=(
                "uq_lineage_invalidation_execution_"
                "intent_decision"
            ),
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            name=(
                "uq_lineage_invalidation_execution_"
                "intents_org_id"
            ),
        ),
        Index(
            (
                "ix_lineage_invalidation_execution_"
                "intents_org_authorized_id"
            ),
            "organization_id",
            "authorized_at",
            "id",
        ),
    )
