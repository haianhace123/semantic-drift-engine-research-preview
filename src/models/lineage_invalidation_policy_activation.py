from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from src.core.database import Base


class LineageInvalidationPolicyActivationRecord(Base):
    __tablename__ = "lineage_invalidation_policy_activations"

    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    policy_external_id = Column(
        String(512),
        primary_key=True,
    )
    active_revision = Column(
        BigInteger,
        nullable=False,
    )
    activated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "active_revision >= 0",
            name=(
                "ck_lineage_invalidation_policy_activation_"
                "revision_nonnegative"
            ),
        ),
    )