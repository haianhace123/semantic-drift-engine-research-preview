"""
SQLAlchemy ORM model for User table.
Production authentication fields, OAuth identities, sessions and soft deletion.
"""

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Enum, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from src.core.database import Base
import uuid


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    email = Column(
        String(255),
        unique=True,
        nullable=False,
        index=True
    )

    password_hash = Column(
        String(255),
        nullable=True
    )

    full_name = Column(
        String(255),
        nullable=False,
        default=""
    )

    role = Column(
        Enum(
            "admin",
            "user",
            "viewer",
            name="user_role_enum"
        ),
        nullable=False,
        default="user",
        server_default="user"
    )

    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true"
    )

    # --------------------------------------------------------
    # EMAIL VERIFICATION
    # --------------------------------------------------------

    email_verified = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false"
    )

    verification_token = Column(
        String(255),
        nullable=True,
        index=True
    )

    verification_token_expires = Column(
        DateTime(timezone=True),
        nullable=True
    )

    verification_code_hash = Column(
        String(64),
        nullable=True,
        index=True
    )

    verification_code_expires = Column(
        DateTime(timezone=True),
        nullable=True
    )

    # --------------------------------------------------------
    # PASSWORD RESET
    # --------------------------------------------------------

    reset_token = Column(
        String(255),
        nullable=True,
        index=True
    )

    reset_token_expires = Column(
        DateTime(timezone=True),
        nullable=True
    )

    # --------------------------------------------------------
    # OAUTH
    # --------------------------------------------------------

    google_id = Column(
        String(255),
        nullable=True,
        unique=True,
        index=True
    )

    github_id = Column(
        String(255),
        nullable=True,
        unique=True,
        index=True
    )

    # --------------------------------------------------------
    # LOGIN SECURITY
    # --------------------------------------------------------

    last_login = Column(
        DateTime(timezone=True),
        nullable=True
    )

    login_attempts = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0"
    )

    locked_until = Column(
        DateTime(timezone=True),
        nullable=True
    )

    # --------------------------------------------------------
    # REFRESH TOKEN
    # --------------------------------------------------------
    # Raw refresh tokens are NEVER stored.
    # Only SHA-256 hash is stored.
    # --------------------------------------------------------

    refresh_token = Column(
        String(64),
        nullable=True,
        index=True
    )

    refresh_token_expires = Column(
        DateTime(timezone=True),
        nullable=True
    )

    # --------------------------------------------------------
    # SOFT DELETE
    # --------------------------------------------------------

    deleted_at = Column(
        DateTime(timezone=True),
        nullable=True,
        index=True
    )

    # --------------------------------------------------------
    # TIMESTAMPS
    # --------------------------------------------------------

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        onupdate=func.now()
    )

    organization = relationship(
        "Organization",
        backref="users"
    )

    def __repr__(self) -> str:
        return (
            f"<User("
            f"email={self.email}, "
            f"role={self.role}"
            f")>"
        )