"""
SQLAlchemy ORM model for Alert.

Alerts are durable intelligence records produced from upstream
Risk, Pattern and Anomaly evidence before external notification
delivery is attempted.
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


ALERT_SOURCE_RISK = "risk"
ALERT_SOURCE_PATTERN = "pattern"
ALERT_SOURCE_ANOMALY = "anomaly"

ALERT_STATUS_PENDING = "pending"
ALERT_STATUS_SENT = "sent"
ALERT_STATUS_READ = "read"
ALERT_STATUS_DISMISSED = "dismissed"


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Optional event-centric context.
    #
    # Risk alerts may reference a SemanticDriftEvent directly.
    # Pattern and Anomaly alerts are not required to map to one
    # arbitrary event.
    event_id = Column(
        UUID(as_uuid=True),
        ForeignKey("semantic_drift_events.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Polymorphic upstream intelligence source.
    #
    # Initial production source types:
    #     risk
    #     pattern
    #     anomaly
    source_type = Column(
        String(32),
        nullable=True,
    )
    source_id = Column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Deterministic identity used for idempotent materialization.
    #
    # Legacy Alert rows may leave this NULL.
    detection_key = Column(
        String(64),
        nullable=True,
    )

    # Alert data.
    alert_type = Column(
        String(50),
        nullable=False,
    )
    title = Column(
        String(255),
        nullable=False,
    )
    message = Column(
        Text,
        nullable=True,
    )
    severity = Column(
        String(20),
        nullable=True,
        default="medium",
    )

    # Context.
    recommendation = Column(
        Text,
        nullable=True,
    )
    affected_entities = Column(
        JSON,
        nullable=True,
        default=list,
    )

    # Temporal time represented by this Alert.
    #
    # This is intentionally separate from created_at, which is
    # the materialization / knowledge time of the Alert row.
    occurred_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Delivery.
    sent_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )
    sent_to = Column(
        JSON,
        nullable=True,
        default=list,
    )

    # User/workflow status.
    status = Column(
        String(20),
        nullable=True,
        default=ALERT_STATUS_PENDING,
    )
    read_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )
    dismissed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Materialization timestamps.
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
        backref="alerts",
    )
    event = relationship(
        "SemanticDriftEvent",
        backref="alerts",
    )

    def __repr__(self) -> str:
        return (
            "<Alert("
            f"type={self.alert_type}, "
            f"source_type={self.source_type}, "
            f"severity={self.severity}, "
            f"status={self.status}"
            ")>"
        )
