"""Security audit events for authentication-sensitive actions."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.core.database import Base


class AuthSecurityEvent(Base):
    """
    Immutable audit record for security-sensitive authentication events.

    This table intentionally stores metadata only. Passwords, refresh
    tokens, OTP codes, verification tokens, reset tokens, API secrets,
    and other authentication credentials must never be written here.
    """

    __tablename__ = "auth_security_events"

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
        nullable=True,
        index=True,
    )

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    event_type = Column(
        String(64),
        nullable=False,
        index=True,
    )

    ip_address = Column(
        String(64),
        nullable=True,
    )

    user_agent = Column(
        Text,
        nullable=True,
    )

    metadata_json = Column(
        JSONB,
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    def __repr__(self) -> str:
        return (
            "<AuthSecurityEvent("
            f"event_type={self.event_type!r}, "
            f"user_id={self.user_id}"
            ")>"
        )