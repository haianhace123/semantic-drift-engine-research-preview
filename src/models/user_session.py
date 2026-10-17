"""Database model for persistent user authentication sessions."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID

from src.core.database import Base


class UserSession(Base):
    """Persisted refresh-token session with device metadata and revocation state."""

    __tablename__ = "user_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    refresh_token_hash = Column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    last_used_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    revoked_at = Column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    ip_address = Column(
        String(64),
        nullable=True,
    )

    user_agent = Column(
        String(1024),
        nullable=True,
    )

    is_revoked = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    def __repr__(self) -> str:
        return (
            f"<UserSession(user_id={self.user_id}, "
            f"revoked={self.is_revoked})>"
        )