"""
Audit log for privacy / cookie consent decisions.
"""

from sqlalchemy import (
    Column,
    String,
    DateTime,
    ForeignKey,
    Boolean,
    Text
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from src.core.database import Base

import uuid


class ConsentLog(Base):
    __tablename__ = "consent_logs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE"
        ),
        nullable=True,
        index=True
    )

    consent_type = Column(
        String(50),
        nullable=False,
        index=True
    )

    granted = Column(
        Boolean,
        nullable=False
    )

    policy_version = Column(
        String(50),
        nullable=False
    )

    ip_address = Column(
        String(64),
        nullable=True
    )

    user_agent = Column(
        Text,
        nullable=True
    )

    metadata_json = Column(
        JSONB,
        nullable=True
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True
    )

    user = relationship(
        "User",
        backref="consent_logs"
    )