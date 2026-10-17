from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


class Entity(Base):
    __tablename__ = "entities"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "domain",
            name="uq_entities_organization_domain",
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

    name = Column(
        String(255),
        nullable=False,
    )

    domain = Column(
        String(255),
        nullable=False,
        index=True,
    )

    category = Column(
        String(100),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at = Column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )

    organization = relationship(
        "Organization",
    )

    monitored_pages = relationship(
        "MonitoredPage",
        back_populates="entity",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            "<Entity("
            f"id={self.id}, "
            f"organization_id={self.organization_id}, "
            f"name={self.name!r}, "
            f"domain={self.domain!r}, "
            f"category={self.category!r}"
            ")>"
        )