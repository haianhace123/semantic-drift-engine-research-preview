from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from src.core.database import Base


class LineageExecutionTargetBindingRecord(Base):
    __tablename__ = "lineage_execution_target_bindings"

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
    source_external_id = Column(
        String,
        nullable=False,
    )
    asset_external_id = Column(
        String,
        nullable=False,
    )
    binding_revision = Column(
        BigInteger,
        nullable=False,
    )
    provider = Column(
        String,
        nullable=False,
    )
    target_kind = Column(
        String,
        nullable=False,
    )
    configuration = Column(
        JSONB,
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "binding_revision > 0",
            name=(
                "ck_lineage_execution_target_bindings_"
                "positive_revision"
            ),
        ),
        UniqueConstraint(
            "organization_id",
            "source_external_id",
            "asset_external_id",
            "binding_revision",
            name="uq_lineage_execution_target_binding_revision",
        ),
        Index(
            "ix_lineage_execution_target_bindings_org_target_revision",
            "organization_id",
            "source_external_id",
            "asset_external_id",
            "binding_revision",
        ),
    )