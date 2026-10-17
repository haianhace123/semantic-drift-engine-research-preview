from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from src.core.database import Base


class OrganizationAlertPolicyRevision(Base):
    """
    Durable organization-scoped Alert policy revision state.

    policy_revision advances whenever Watchlist configuration changes
    candidate policy.

    reconciled_revision advances only after Alert policy reconciliation
    succeeds in the caller-owned transaction.
    """

    __tablename__ = "organization_alert_policy_revisions"

    __table_args__ = (
        CheckConstraint(
            "policy_revision >= 0",
            name="ck_alert_policy_revision_nonnegative",
        ),
        CheckConstraint(
            "reconciled_revision >= 0",
            name="ck_alert_policy_reconciled_revision_nonnegative",
        ),
        CheckConstraint(
            "reconciled_revision <= policy_revision",
            name="ck_alert_policy_reconciled_lte_policy",
        ),
    )

    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )

    policy_revision = Column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )

    reconciled_revision = Column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    reconciled_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            "<OrganizationAlertPolicyRevision("
            f"organization_id={self.organization_id}, "
            f"policy_revision={self.policy_revision}, "
            f"reconciled_revision={self.reconciled_revision}"
            ")>"
        )
