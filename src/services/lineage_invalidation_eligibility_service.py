from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from src.domain.lineage_invalidation import (
    LineageInvalidationDecision,
)
from src.services.lineage_invalidation_policy_activation_service import (
    load_active_lineage_invalidation_policy,
)
from src.services.lineage_projection_service import (
    load_current_lineage_observation,
)


LineageInvalidationEligibilityReason = Literal[
    "eligible",
    "no_action",
    "policy_not_active",
    "policy_revision_superseded",
    "policy_revision_not_active",
    "target_authority_missing",
    "target_not_current",
]


@dataclass(frozen=True)
class LineageInvalidationEligibility:
    eligible: bool
    reason_code: LineageInvalidationEligibilityReason


def evaluate_lineage_invalidation_eligibility(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision: LineageInvalidationDecision,
) -> LineageInvalidationEligibility:
    if decision.disposition == "no_action":
        return LineageInvalidationEligibility(
            eligible=False,
            reason_code="no_action",
        )

    active_policy = load_active_lineage_invalidation_policy(
        db,
        organization_id=organization_id,
        policy_external_id=decision.policy.policy_external_id,
    )

    if active_policy is None:
        return LineageInvalidationEligibility(
            eligible=False,
            reason_code="policy_not_active",
        )

    if decision.policy.revision < active_policy.policy.revision:
        return LineageInvalidationEligibility(
            eligible=False,
            reason_code="policy_revision_superseded",
        )

    if decision.policy.revision > active_policy.policy.revision:
        return LineageInvalidationEligibility(
            eligible=False,
            reason_code="policy_revision_not_active",
        )

    current_target = load_current_lineage_observation(
        db,
        organization_id=organization_id,
        source_external_id=decision.target.source_external_id,
        asset_external_id=decision.target.asset_external_id,
        observation_kind="materialization",
    )

    if current_target is None:
        return LineageInvalidationEligibility(
            eligible=False,
            reason_code="target_authority_missing",
        )

    if (
        current_target.revision_external_id
        != decision.target.revision_external_id
    ):
        return LineageInvalidationEligibility(
            eligible=False,
            reason_code="target_not_current",
        )

    return LineageInvalidationEligibility(
        eligible=True,
        reason_code="eligible",
    )