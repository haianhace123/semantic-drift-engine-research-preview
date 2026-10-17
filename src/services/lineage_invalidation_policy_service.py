from __future__ import annotations

from dataclasses import dataclass

from src.domain.lineage_invalidation import (
    LineageInvalidationDisposition,
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_observation import (
    LineageAssetKind,
)


DEFAULT_LINEAGE_INVALIDATION_POLICY = (
    LineageInvalidationPolicyIdentity(
        policy_external_id="sde-default-lineage-invalidation",
        revision=1,
    )
)

CAUSAL_DEPENDENCY_CHANGED_REASON_CODE = (
    "causal_dependency_changed"
)
UNSUPPORTED_TARGET_KIND_REASON_CODE = (
    "unsupported_target_kind"
)


@dataclass(frozen=True)
class LineageInvalidationPolicyEvaluation:
    disposition: LineageInvalidationDisposition
    reason_code: str


def evaluate_lineage_invalidation_policy(
    *,
    policy: LineageInvalidationPolicyIdentity,
    target_asset_kind: LineageAssetKind,
) -> LineageInvalidationPolicyEvaluation:
    if policy != DEFAULT_LINEAGE_INVALIDATION_POLICY:
        raise ValueError(
            "Unsupported lineage invalidation policy identity."
        )

    if target_asset_kind == "index":
        return LineageInvalidationPolicyEvaluation(
            disposition="invalidate",
            reason_code=CAUSAL_DEPENDENCY_CHANGED_REASON_CODE,
        )

    return LineageInvalidationPolicyEvaluation(
        disposition="no_action",
        reason_code=UNSUPPORTED_TARGET_KIND_REASON_CODE,
    )