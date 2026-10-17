from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    String,
    Text,
)
from sqlalchemy.sql import func

from src.core.database import Base


WORKER_KIND_VECTOR = "vector"
WORKER_KIND_ALERT_DELIVERY = "alert_delivery"
WORKER_KIND_CRAWLER = "crawler"
WORKER_KIND_LINEAGE_INVALIDATION = "lineage_invalidation"
WORKER_KIND_EMAIL = "email"

WORKER_KINDS = frozenset(
    {
        WORKER_KIND_VECTOR,
        WORKER_KIND_ALERT_DELIVERY,
        WORKER_KIND_CRAWLER,
        WORKER_KIND_LINEAGE_INVALIDATION,
        WORKER_KIND_EMAIL,
    }
)


class WorkerOperationalState(Base):
    """
    Durable operational state for one background worker process incarnation.

    Rows contain runtime facts only. Health is derived by readers from heartbeat
    age and failure history rather than persisted as a second source of truth.
    """

    __tablename__ = "worker_operational_states"

    __table_args__ = (
        CheckConstraint(
            "consecutive_failures >= 0",
            name=(
                "worker_operational_states_"
                "consecutive_failures_check"
            ),
        ),
    )

    worker_kind = Column(
        String(64),
        primary_key=True,
    )

    worker_instance_id = Column(
        String(255),
        primary_key=True,
    )

    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    last_heartbeat_at = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    last_cycle_started_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_cycle_completed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_success_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    consecutive_failures = Column(
        Integer,
        nullable=False,
        server_default="0",
    )

    last_failure_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_error = Column(
        Text,
        nullable=True,
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            "<WorkerOperationalState("
            f"worker_kind={self.worker_kind!r}, "
            f"worker_instance_id={self.worker_instance_id!r}, "
            f"consecutive_failures={self.consecutive_failures}"
            ")>"
        )
