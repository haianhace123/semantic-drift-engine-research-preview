"""
SQLAlchemy ORM model for Investigation.
User-initiated investigations into changes or patterns.
"""

from sqlalchemy import Column, String, DateTime, ForeignKey, JSON, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from src.core.database import Base
import uuid


class Investigation(Base):
    __tablename__ = "investigations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # Scope
    entity_ids = Column(JSON, default=[])
    event_ids = Column(JSON, default=[])
    date_range_start = Column(DateTime(timezone=True), nullable=True)
    date_range_end = Column(DateTime(timezone=True), nullable=True)
    categories = Column(JSON, default=[])
    
    # Findings
    findings = Column(Text, nullable=True)
    key_insights = Column(JSON, default=[])
    related_patterns = Column(JSON, default=[])
    
    # Status
    status = Column(String(20), default="active")  # active, completed, archived
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Flags
    is_archived = Column(Boolean, default=False)
    is_shared = Column(Boolean, default=False)
    
    organization = relationship("Organization", backref="investigations")

    def __repr__(self) -> str:
        return f"<Investigation(name={self.name}, status={self.status})>"