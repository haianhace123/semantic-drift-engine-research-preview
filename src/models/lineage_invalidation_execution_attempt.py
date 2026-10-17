from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from src.core.database import Base


class LineageInvalidationExecutionAttemptRecord(Base):
    __tablename__ = "lineage_invalidation_execution_attempts"

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
    intent_id = Column(
        UUID(as_uuid=True),
        nullable=False,
    )
    attempt_number = Column(
        BigInteger,
        nullable=False,
    )

    binding_revision = Column(
        BigInteger,
        nullable=False,
    )

    claimed_at = Column(
        DateTime(timezone=True),
        nullable=False,
    )
    lease_expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            (
                "organization_id",
                "intent_id",
            ),
            (
                "lineage_invalidation_execution_intents.organization_id",
                "lineage_invalidation_execution_intents.id",
            ),
            name=(
                "lineage_invalidation_execution_attempts_"
                "intent_fkey"
            ),
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "attempt_number > 0",
            name=(
                "ck_lineage_invalidation_execution_"
                "attempt_number_positive"
            ),
        ),
        CheckConstraint(
            "binding_revision > 0",
            name=(
                "ck_lineage_invalidation_execution_"
                "attempt_binding_revision_positive"
            ),
        ),
        CheckConstraint(
            "lease_expires_at > claimed_at",
            name=(
                "ck_lineage_invalidation_execution_"
                "attempt_lease_window"
            ),
        ),
        UniqueConstraint(
            "organization_id",
            "intent_id",
            "attempt_number",
            name=(
                "uq_lineage_invalidation_execution_"
                "attempt_number"
            ),
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            name=(
                "uq_lineage_invalidation_execution_"
                "attempts_org_id"
            ),
        ),
        Index(
            (
                "ix_lineage_invalidation_execution_attempts_"
                "org_intent_claimed_i"
            ),
            "organization_id",
            "intent_id",
            "claimed_at",
            "id",
        ),
    )