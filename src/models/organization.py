"""
SQLAlchemy ORM model for Organization table.
Stores multi-tenant organization information.
"""

from sqlalchemy import Column, String, DateTime, Integer, Boolean, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from src.core.database import Base
import uuid


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    billing_email = Column(String(255), nullable=False)
    subscription_tier = Column(String(50), default="free")
    quota_urls = Column(Integer, default=10)
    quota_requests = Column(Integer, default=1000)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<Organization(name={self.name}, slug={self.slug})>"