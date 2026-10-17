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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


class LineageObservationRecord(Base):
    __tablename__ = "lineage_observations"

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

    observation_kind = Column(
        String(32),
        nullable=False,
    )

    source_external_id = Column(
        String(512),
        nullable=False,
    )
    snapshot_external_id = Column(
        String(512),
        nullable=False,
    )

    asset_external_id = Column(
        String(512),
        nullable=False,
    )
    asset_kind = Column(
        String(32),
        nullable=False,
    )
    revision_external_id = Column(
        String(512),
        nullable=False,
    )

    sequence = Column(
        BigInteger,
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    dependencies = relationship(
        "LineageObservationDependencyRecord",
        back_populates="observation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "observation_kind IN ('materialization', 'consumption')",
            name="ck_lineage_observations_kind",
        ),
        CheckConstraint(
            "asset_kind IN ('chunk', 'index', 'agent', 'workflow')",
            name="ck_lineage_observations_asset_kind",
        ),
        CheckConstraint(
            "sequence >= 0",
            name="ck_lineage_observations_sequence_nonnegative",
        ),
        UniqueConstraint(
            "organization_id",
            "source_external_id",
            "asset_external_id",
            "observation_kind",
            "sequence",
            name="uq_lineage_observation_identity",
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            name="uq_lineage_observations_org_id",
        ),
        Index(
            "ix_lineage_observations_org_created_id",
            "organization_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_lineage_observations_org_subject_sequence",
            "organization_id",
            "source_external_id",
            "asset_external_id",
            "observation_kind",
            sequence.desc(),
        ),
    )


class LineageObservationDependencyRecord(Base):
    __tablename__ = "lineage_observation_dependencies"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    observation_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "lineage_observations.id",
            ondelete="CASCADE",
        ),
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
    dependency_type = Column(
        String(128),
        nullable=False,
    )

    observation = relationship(
        "LineageObservationRecord",
        back_populates="dependencies",
    )

    __table_args__ = (
        UniqueConstraint(
            "observation_id",
            "asset_external_id",
            "revision_external_id",
            "dependency_type",
            name="uq_lineage_observation_dependency",
        ),
        Index(
            "ix_lineage_dependencies_observation_id",
            "observation_id",
        ),
        Index(
            "ix_lineage_dependencies_exact_revision",
            "asset_external_id",
            "revision_external_id",
            "observation_id",
        ),
    )
