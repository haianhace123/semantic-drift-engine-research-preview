"""
SQLAlchemy ORM model for Anomaly.
Detected anomalies in change patterns.
"""

from sqlalchemy import Column, String, DateTime, ForeignKey, JSON, Integer, Float, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from src.core.database import Base
import uuid


class Anomaly(Base):
    __tablename__ = "anomalies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    # Related event
    event_id = Column(
        UUID(as_uuid=True),
        ForeignKey("semantic_drift_events.id", ondelete="CASCADE"),
        nullable=False
    )
    
    # Anomaly data
    anomaly_type = Column(String(50), nullable=False)  # frequency, magnitude, timing, categorical
    detection_key = Column(
        String(64),
        nullable=True,
    )
    description = Column(Text, nullable=True)
    
    # Metrics
    expected_value = Column(Float, nullable=True)
    actual_value = Column(Float, nullable=True)
    deviation_score = Column(Float, default=0.0)
    significance = Column(Float, default=0.0)
    
    # Context
    baseline_days = Column(Integer, default=90)
    window_days = Column(
        Integer,
        nullable=False,
        default=7,
    )
    detection_method = Column(String(50), nullable=True)
    
    # Timestamps
    detected_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Flags
    is_confirmed = Column(Boolean, default=False)
    is_dismissed = Column(Boolean, default=False)
    is_archived = Column(Boolean, default=False)
    
    organization = relationship("Organization", backref="anomalies")
    event = relationship("SemanticDriftEvent", backref="anomaly")

    def __repr__(self) -> str:
        return f"<Anomaly(type={self.anomaly_type}, significance={self.significance})>"