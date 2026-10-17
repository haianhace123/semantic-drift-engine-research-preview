"""
SQLAlchemy ORM model for Watchlist.
User-defined watchlist of entities or documents.
"""

from sqlalchemy import Column, String, DateTime, ForeignKey, JSON, Integer, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from src.core.database import Base
import uuid


class Watchlist(Base):
    __tablename__ = "watchlists"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # Watchlist items
    entity_ids = Column(JSON, default=[])  # List of entity IDs
    page_ids = Column(JSON, default=[])  # List of monitored page IDs
    categories = Column(JSON, default=[])  # List of categories
    
    # Settings
    alert_on_high_risk = Column(Boolean, default=True)
    alert_on_medium_risk = Column(Boolean, default=False)
    alert_on_pattern = Column(Boolean, default=True)
    alert_on_anomaly = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    
    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    
    # Flags
    is_active = Column(Boolean, default=True)
    is_archived = Column(Boolean, default=False)
    
    organization = relationship("Organization", backref="watchlists")

    def __repr__(self) -> str:
        return f"<Watchlist(name={self.name})>"