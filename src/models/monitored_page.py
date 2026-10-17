"""
SQLAlchemy ORM model for MonitoredPage table.
Represents a specific URL being tracked for semantic drift.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    JSON,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from src.core.database import Base
import uuid


class MonitoredPage(Base):
    __tablename__ = "monitored_pages"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "entity_id",
            "url",
            name="uq_monitored_pages_organization_entity_url",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    entity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    url = Column(Text, nullable=False)
    page_type = Column(
        Enum("pricing", "tos", "privacy", "sla", "docs", name="page_type_enum"),
        nullable=False
    )
    crawl_frequency = Column(
        Enum(
            "hourly",
            "daily",
            "weekly",
            name="crawl_freq_enum",
        ),
        nullable=False,
        default="daily",
    )
    is_active = Column(Boolean, default=True)
    custom_rules = Column(JSON, default={})
    last_crawled_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    next_crawl_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Last finalized attempt, not the current live claim. Historical rows have
    # no outcome until the new scheduler has successfully finalized a cycle.
    last_crawl_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_crawl_status = Column(String(16), nullable=True)
    last_crawl_failure_code = Column(String(32), nullable=True)
    consecutive_crawl_failures = Column(Integer, nullable=False, default=0, server_default="0")

    crawl_claim_token = Column(
        UUID(as_uuid=True),
        nullable=True,
    )

    crawl_claimed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    crawl_claim_expires_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    entity = relationship("Entity", back_populates="monitored_pages")
    organization = relationship("Organization", backref="monitored_pages")

    def __repr__(self) -> str:
        return f"<MonitoredPage(url={self.url}, type={self.page_type})>"