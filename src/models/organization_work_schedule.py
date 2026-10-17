from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from src.core.database import Base


WORK_KIND_INTELLIGENCE = "intelligence"
WORK_KIND_ALERT_RECONCILIATION = "alert_reconciliation"

ORGANIZATION_WORK_KINDS = frozenset(
    {
        WORK_KIND_INTELLIGENCE,
        WORK_KIND_ALERT_RECONCILIATION,
    }
)


class OrganizationWorkSchedule(Base):
    """
    Durable retry scheduling state for organization-scoped worker work.

    Scheduling is keyed by organization and work kind so failure in one
    downstream lifecycle cannot delay independent work for the same tenant.
    """

    __tablename__ = "organization_work_schedules"

    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )

    work_kind = Column(
        String(64),
        primary_key=True,
    )

    retry_after = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            "<OrganizationWorkSchedule("
            f"organization_id={self.organization_id}, "
            f"work_kind={self.work_kind!r}, "
            f"retry_after={self.retry_after}"
            ")>"
        )
