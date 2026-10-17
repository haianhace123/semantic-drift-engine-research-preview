from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


class LineageInvalidationDecisionRecord(Base):
    __tablename__ = "lineage_invalidation_decisions"

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

    root_source_external_id = Column(
        String(512),
        nullable=False,
    )
    root_asset_external_id = Column(
        String(512),
        nullable=False,
    )
    root_revision_external_id = Column(
        String(512),
        nullable=False,
    )

    target_source_external_id = Column(
        String(512),
        nullable=False,
    )
    target_asset_external_id = Column(
        String(512),
        nullable=False,
    )
    target_revision_external_id = Column(
        String(512),
        nullable=False,
    )

    policy_external_id = Column(
        String(512),
        nullable=False,
    )
    policy_revision = Column(
        BigInteger,
        nullable=False,
    )

    disposition = Column(
        String(32),
        nullable=False,
    )
    reason_code = Column(
        String(128),
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    causal_path = relationship(
        "LineageInvalidationDecisionPathRevisionRecord",
        back_populates="decision",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by=(
            "LineageInvalidationDecisionPathRevisionRecord.ordinal"
        ),
    )

    __table_args__ = (
        CheckConstraint(
            "policy_revision >= 0",
            name=(
                "ck_lineage_invalidation_decisions_"
                "policy_revision_nonnegative"
            ),
        ),
        CheckConstraint(
            (
                "disposition IN "
                "('no_action', 'invalidate', 'rematerialize')"
            ),
            name="ck_lineage_invalidation_decisions_disposition",
        ),
        UniqueConstraint(
            "organization_id",
            "root_source_external_id",
            "root_asset_external_id",
            "root_revision_external_id",
            "target_source_external_id",
            "target_asset_external_id",
            "target_revision_external_id",
            "policy_external_id",
            "policy_revision",
            name="uq_lineage_invalidation_decision_identity",
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            name="uq_lineage_invalidation_decisions_org_id",
        ),
        Index(
            "ix_lineage_invalidation_decisions_org_created_id",
            "organization_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_lineage_invalidation_decisions_org_target",
            "organization_id",
            "target_source_external_id",
            "target_asset_external_id",
            "target_revision_external_id",
        ),
    )


class LineageInvalidationDecisionPathRevisionRecord(Base):
    __tablename__ = (
        "lineage_invalidation_decision_path_revisions"
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    decision_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "lineage_invalidation_decisions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    ordinal = Column(
        Integer,
        nullable=False,
    )
    source_external_id = Column(
        String(512),
        nullable=False,
    )
    asset_external_id = Column(
        String(512),
        nullable=False,
    )
    revision_external_id = Column(
        String(512),
        nullable=False,
    )

    decision = relationship(
        "LineageInvalidationDecisionRecord",
        back_populates="causal_path",
    )

    __table_args__ = (
        CheckConstraint(
            "ordinal >= 0",
            name=(
                "ck_lineage_invalidation_path_"
                "ordinal_nonnegative"
            ),
        ),
        UniqueConstraint(
            "decision_id",
            "ordinal",
            name="uq_lineage_invalidation_path_ordinal",
        ),
        Index(
            "ix_lineage_invalidation_path_decision_ordinal",
            "decision_id",
            "ordinal",
        ),
    )