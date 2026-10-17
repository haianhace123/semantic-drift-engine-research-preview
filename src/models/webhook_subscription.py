"""
SQLAlchemy ORM model for WebhookSubscription table.
Stores webhook endpoints for real-time notifications.
"""

from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    LargeBinary,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from src.core.database import Base
import uuid


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    url = Column(Text, nullable=False)
    events = Column(ARRAY(String(50)), default=["high_risk"])
    secret_ciphertext = Column(LargeBinary, nullable=False)
    secret_nonce = Column(LargeBinary, nullable=False)
    secret_encryption_version = Column(SmallInteger, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization", backref="webhook_subscriptions")

    def __repr__(self) -> str:
        return f"<WebhookSubscription(url={self.url})>"