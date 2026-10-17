"""
SQLAlchemy ORM model for durable Alert delivery.

AlertDelivery is intentionally separate from Alert:

- Alert is durable intelligence.
- AlertDelivery is one durable fan-out destination.
- Delivery retry state belongs here, not on Alert.
- External network I/O must never happen while creating this row.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


ALERT_DELIVERY_DESTINATION_WEBHOOK = "webhook"

ALERT_DELIVERY_STATUS_PENDING = "pending"
ALERT_DELIVERY_STATUS_PROCESSING = "processing"
ALERT_DELIVERY_STATUS_DELIVERED = "delivered"
ALERT_DELIVERY_STATUS_RETRYABLE = "retryable"
ALERT_DELIVERY_STATUS_FAILED = "failed"


class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"

    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_alert_deliveries_attempt_count",
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

    alert_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "alerts.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    destination_type = Column(
        String(32),
        nullable=False,
    )

    destination_id = Column(
        UUID(as_uuid=True),
        nullable=False,
    )

    delivery_key = Column(
        String(64),
        nullable=False,
    )

    status = Column(
        String(20),
        nullable=False,
        default=ALERT_DELIVERY_STATUS_PENDING,
    )

    payload = Column(
        JSONB,
        nullable=False,
    )

    attempt_count = Column(
        Integer,
        nullable=False,
        default=0,
    )

    next_attempt_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    claimed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_attempt_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    delivered_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_error = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    organization = relationship(
        "Organization",
        backref="alert_deliveries",
    )

    alert = relationship(
        "Alert",
        backref="deliveries",
    )

    def __repr__(self) -> str:
        return (
            "<AlertDelivery("
            f"alert_id={self.alert_id}, "
            f"destination_type={self.destination_type}, "
            f"status={self.status}, "
            f"attempt_count={self.attempt_count}"
            ")>"
        )