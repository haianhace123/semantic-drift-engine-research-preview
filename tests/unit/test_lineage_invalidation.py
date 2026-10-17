import pytest

from src.domain.lineage_invalidation import (
    LineageInvalidationDecision,
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)


def _revision(
    asset_external_id: str,
) -> LineageRevisionIdentity:
    return LineageRevisionIdentity(
        source_external_id="source-a",
        asset_external_id=asset_external_id,
        revision_external_id=f"{asset_external_id}@v1",
    )


def _policy() -> LineageInvalidationPolicyIdentity:
    return LineageInvalidationPolicyIdentity(
        policy_external_id="default-lineage-policy",
        revision=0,
    )


@pytest.mark.parametrize(
    "disposition",
    (
        "no_action",
        "invalidate",
        "rematerialize",
    ),
)
def test_invalidation_decision_accepts_supported_dispositions(
    disposition,
):
    root = _revision("chunk-a")
    target = _revision("index-a")

    decision = LineageInvalidationDecision(
        root=root,
        target=target,
        disposition=disposition,
        reason_code=" causal_dependency_changed ",
        causal_path=(
            root,
            target,
        ),
        policy=_policy(),
    )

    assert decision.disposition == disposition
    assert decision.reason_code == "causal_dependency_changed"
    assert decision.causal_path == (
        root,
        target,
    )


def test_invalidation_decision_accepts_multi_hop_causal_path():
    root = _revision("chunk-a")
    intermediate = _revision("index-a")
    target = _revision("agent-a")

    decision = LineageInvalidationDecision(
        root=root,
        target=target,
        disposition="invalidate",
        reason_code="causal_dependency_changed",
        causal_path=(
            root,
            intermediate,
            target,
        ),
        policy=_policy(),
    )

    assert decision.causal_path == (
        root,
        intermediate,
        target,
    )


@pytest.mark.parametrize(
    "reason_code",
    (
        "",
        " ",
    ),
)
def test_invalidation_decision_rejects_empty_reason_code(
    reason_code,
):
    root = _revision("chunk-a")
    target = _revision("index-a")

    with pytest.raises(
        ValueError,
        match="reason_code must not be empty",
    ):
        LineageInvalidationDecision(
            root=root,
            target=target,
            disposition="invalidate",
            reason_code=reason_code,
            causal_path=(
                root,
                target,
            ),
            policy=_policy(),
        )


def test_invalidation_decision_rejects_empty_causal_path():
    root = _revision("chunk-a")
    target = _revision("index-a")

    with pytest.raises(
        ValueError,
        match="causal_path must not be empty",
    ):
        LineageInvalidationDecision(
            root=root,
            target=target,
            disposition="invalidate",
            reason_code="causal_dependency_changed",
            causal_path=(),
            policy=_policy(),
        )


def test_invalidation_decision_requires_path_to_start_at_root():
    root = _revision("chunk-a")
    other = _revision("chunk-b")
    target = _revision("index-a")

    with pytest.raises(
        ValueError,
        match="causal_path must start with root",
    ):
        LineageInvalidationDecision(
            root=root,
            target=target,
            disposition="invalidate",
            reason_code="causal_dependency_changed",
            causal_path=(
                other,
                target,
            ),
            policy=_policy(),
        )


def test_invalidation_decision_requires_path_to_end_at_target():
    root = _revision("chunk-a")
    target = _revision("index-a")
    other = _revision("agent-a")

    with pytest.raises(
        ValueError,
        match="causal_path must end with target",
    ):
        LineageInvalidationDecision(
            root=root,
            target=target,
            disposition="invalidate",
            reason_code="causal_dependency_changed",
            causal_path=(
                root,
                other,
            ),
            policy=_policy(),
        )


def test_invalidation_decision_rejects_root_as_target():
    root = _revision("chunk-a")

    with pytest.raises(
        ValueError,
        match="target must differ from root",
    ):
        LineageInvalidationDecision(
            root=root,
            target=root,
            disposition="no_action",
            reason_code="causal_dependency_changed",
            causal_path=(
                root,
            ),
            policy=_policy(),
        )


def test_invalidation_decision_rejects_repeated_revision_in_path():
    root = _revision("chunk-a")
    intermediate = _revision("index-a")
    target = _revision("agent-a")

    with pytest.raises(
        ValueError,
        match="must not contain repeated revisions",
    ):
        LineageInvalidationDecision(
            root=root,
            target=target,
            disposition="invalidate",
            reason_code="causal_dependency_changed",
            causal_path=(
                root,
                intermediate,
                root,
                target,
            ),
            policy=_policy(),
        )


def test_invalidation_decision_rejects_unsupported_disposition():
    root = _revision("chunk-a")
    target = _revision("index-a")

    with pytest.raises(
        ValueError,
        match="Unsupported invalidation disposition",
    ):
        LineageInvalidationDecision(
            root=root,
            target=target,
            disposition="delete_everything",
            reason_code="causal_dependency_changed",
            causal_path=(
                root,
                target,
            ),
            policy=_policy(),
        )

def test_invalidation_policy_identity_normalizes_external_id():
    policy = LineageInvalidationPolicyIdentity(
        policy_external_id=" default-lineage-policy ",
        revision=0,
    )

    assert policy.policy_external_id == "default-lineage-policy"
    assert policy.revision == 0


@pytest.mark.parametrize(
    "policy_external_id",
    (
        "",
        " ",
    ),
)
def test_invalidation_policy_identity_rejects_empty_external_id(
    policy_external_id,
):
    with pytest.raises(
        ValueError,
        match="policy_external_id must not be empty",
    ):
        LineageInvalidationPolicyIdentity(
            policy_external_id=policy_external_id,
            revision=0,
        )


def test_invalidation_policy_identity_rejects_negative_revision():
    with pytest.raises(
        ValueError,
        match="policy revision must not be negative",
    ):
        LineageInvalidationPolicyIdentity(
            policy_external_id="default-lineage-policy",
            revision=-1,
        )


def test_invalidation_policy_identity_accepts_maximum_external_id_length():
    policy = LineageInvalidationPolicyIdentity(
        policy_external_id="p" * 512,
        revision=0,
    )

    assert len(policy.policy_external_id) == 512


def test_invalidation_policy_identity_rejects_oversized_external_id():
    with pytest.raises(
        ValueError,
        match="policy_external_id must not exceed 512 characters",
    ):
        LineageInvalidationPolicyIdentity(
            policy_external_id="p" * 513,
            revision=0,
        )


def test_invalidation_decision_accepts_maximum_reason_code_length():
    root = _revision("chunk-a")
    target = _revision("index-a")

    decision = LineageInvalidationDecision(
        root=root,
        target=target,
        disposition="invalidate",
        reason_code="r" * 128,
        causal_path=(
            root,
            target,
        ),
        policy=_policy(),
    )

    assert len(decision.reason_code) == 128


def test_invalidation_decision_rejects_oversized_reason_code():
    root = _revision("chunk-a")
    target = _revision("index-a")

    with pytest.raises(
        ValueError,
        match="reason_code must not exceed 128 characters",
    ):
        LineageInvalidationDecision(
            root=root,
            target=target,
            disposition="invalidate",
            reason_code="r" * 129,
            causal_path=(
                root,
                target,
            ),
            policy=_policy(),
        )
