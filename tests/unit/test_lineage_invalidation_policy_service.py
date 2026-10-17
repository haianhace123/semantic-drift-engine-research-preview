import pytest

from src.domain.lineage_invalidation import (
    LineageInvalidationPolicyIdentity,
)
from src.services.lineage_invalidation_policy_service import (
    CAUSAL_DEPENDENCY_CHANGED_REASON_CODE,
    DEFAULT_LINEAGE_INVALIDATION_POLICY,
    UNSUPPORTED_TARGET_KIND_REASON_CODE,
    evaluate_lineage_invalidation_policy,
)


def test_default_policy_invalidates_index_target() -> None:
    evaluation = evaluate_lineage_invalidation_policy(
        policy=DEFAULT_LINEAGE_INVALIDATION_POLICY,
        target_asset_kind="index",
    )

    assert evaluation.disposition == "invalidate"
    assert (
        evaluation.reason_code
        == CAUSAL_DEPENDENCY_CHANGED_REASON_CODE
    )


@pytest.mark.parametrize(
    "asset_kind",
    [
        "chunk",
        "agent",
        "workflow",
    ],
)
def test_default_policy_does_not_execute_unsupported_target_kind(
    asset_kind: str,
) -> None:
    evaluation = evaluate_lineage_invalidation_policy(
        policy=DEFAULT_LINEAGE_INVALIDATION_POLICY,
        target_asset_kind=asset_kind,
    )

    assert evaluation.disposition == "no_action"
    assert (
        evaluation.reason_code
        == UNSUPPORTED_TARGET_KIND_REASON_CODE
    )


def test_unknown_policy_identity_fails_closed() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "Unsupported lineage invalidation policy identity"
        ),
    ):
        evaluate_lineage_invalidation_policy(
            policy=LineageInvalidationPolicyIdentity(
                policy_external_id="unknown-policy",
                revision=1,
            ),
            target_asset_kind="index",
        )