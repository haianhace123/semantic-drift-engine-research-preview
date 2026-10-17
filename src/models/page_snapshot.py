"""
SQLAlchemy ORM model for PageSnapshot table.

Stores historical versions of monitored page content together
with the vector representation used by the semantic drift layer.
"""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


EMBEDDING_DIMENSIONS = 1536

EMBEDDING_STATUS_PENDING = "pending"
EMBEDDING_STATUS_PROCESSING = "processing"
EMBEDDING_STATUS_COMPLETED = "completed"
EMBEDDING_STATUS_FAILED = "failed"


class PageSnapshot(Base):
    __tablename__ = "page_snapshots"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    monitored_page_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "monitored_pages.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
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

    # -----------------------------------------------------------------------
    # Snapshot content
    # -----------------------------------------------------------------------

    content_hash = Column(
        String(64),
        nullable=False,
        index=True,
    )

    raw_content = Column(
        Text,
        nullable=True,
    )

    content_size = Column(
        Integer,
        default=0,
    )

    raw_content_path = Column(
        Text,
        nullable=True,
    )

    # -----------------------------------------------------------------------
    # Vector semantic representation
    #
    # Existing historical snapshots remain pending until the embedding
    # worker backfills them. Schema migrations must never call an external
    # embedding provider.
    # -----------------------------------------------------------------------

    embedding = Column(
        Vector(EMBEDDING_DIMENSIONS),
        nullable=True,
    )

    embedding_model = Column(
        String(255),
        nullable=True,
    )

    embedding_dimensions = Column(
        Integer,
        nullable=True,
    )

    embedded_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    embedding_status = Column(
        String(20),
        nullable=False,
        default=EMBEDDING_STATUS_PENDING,
        server_default=EMBEDDING_STATUS_PENDING,
        index=True,
    )

    embedding_error = Column(
        Text,
        nullable=True,
    )

    embedding_attempts = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    # -----------------------------------------------------------------------
    # Timestamps
    # -----------------------------------------------------------------------

    crawled_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # -----------------------------------------------------------------------
    # Relationships
    # -----------------------------------------------------------------------

    monitored_page = relationship(
        "MonitoredPage",
        backref="snapshots",
    )

    organization = relationship(
        "Organization",
    )

    def __repr__(self) -> str:
        return (
            "<PageSnapshot("
            f"hash={self.content_hash[:8]}..., "
            f"crawled={self.crawled_at}, "
            f"embedding_status={self.embedding_status}"
            ")>"
        )