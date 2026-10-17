"""
SQLAlchemy ORM model for Pattern.
Detected patterns across multiple drift events.
"""

from sqlalchemy import Column, String, DateTime, ForeignKey, JSON, Integer, Text, Boolean, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from src.core.database import Base
import uuid


class Pattern(Base):
    __tablename__ = "patterns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # Pattern data
    pattern_type = Column(String(50), nullable=False)  # temporal, semantic, categorical
    detection_key = Column(
        String(64),
        nullable=True,
    )

    detection_method = Column(
        String(64),
        nullable=True,
    )
    event_ids = Column(JSON, default=[])  # List of event IDs in this pattern
    entities = Column(JSON, default=[])  # List of entity names
    categories = Column(JSON, default=[])  # List of categories
    
    # Statistics
    event_count = Column(Integer, default=0)
    entity_count = Column(Integer, default=0)
    time_span_days = Column(Integer, default=0)
    significance_score = Column(Float, default=0.0)
    
    # Temporal bounds
    first_event_at = Column(DateTime(timezone=True), nullable=True)
    last_event_at = Column(DateTime(timezone=True), nullable=True)
    
    # Metadata
    summary = Column(Text, nullable=True)
    detected_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    is_active = Column(Boolean, default=True)
    is_archived = Column(Boolean, default=False)
    
    organization = relationship("Organization", backref="patterns")

    def __repr__(self) -> str:
        return f"<Pattern(name={self.name}, count={self.event_count})>"