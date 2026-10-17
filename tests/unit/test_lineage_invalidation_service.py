import pytest

from src.domain.lineage_invalidation import (
    LineageInvalidationPolicyIdentity,
)

from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.services.lineage_impact_service import (
    LineageImpactAnalysis,
    LineageImpactPath,
)
from src.services.lineage_invalidation_service import (
    derive_lineage_invalidation_decisions,
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
        revision=7,
    )


def test_derives_decision_for_each_impacted_revision():
    root = _revision("chunk-a")
    index = _revision("index-a")
    agent = _revision("agent-a")

    impact = LineageImpactAnalysis(
        root=root,
        impacted_revisions=(
            agent,
            index,
        ),
        paths=(
            LineageImpactPath(
                root=root,
                target=index,
                revisions=(
                    root,
                    index,
                ),
            ),
            LineageImpactPath(
                root=root,
                target=agent,
                revisions=(
                    root,
                    index,
                    agent,
                ),
            ),
        ),
    )

    decisions = derive_lineage_invalidation_decisions(
        impact=impact,
        policy=lambda path: (
            (
                "rematerialize"
                if path.target == index
                else "invalidate"
            ),
            "causal_dependency_changed",
        ),
        policy_identity=_policy(),
    )

    assert tuple(
        decision.target
        for decision in decisions
    ) == (
        agent,
        index,
    )
    assert tuple(
        decision.disposition
        for decision in decisions
    ) == (
        "invalidate",
        "rematerialize",
    )
    assert tuple(
        decision.policy
        for decision in decisions
    ) == (
        _policy(),
        _policy(),
    )


def test_policy_receives_exact_causal_path():
    root = _revision("chunk-a")
    index = _revision("index-a")
    agent = _revision("agent-a")

    impact_path = LineageImpactPath(
        root=root,
        target=agent,
        revisions=(
            root,
            index,
            agent,
        ),
    )
    impact = LineageImpactAnalysis(
        root=root,
        impacted_revisions=(agent,),
        paths=(impact_path,),
    )
    observed_paths = []

    derive_lineage_invalidation_decisions(
        impact=impact,
        policy=lambda path: (
            observed_paths.append(path)
            or (
                "no_action",
                "policy_exempt",
            )
        ),
        policy_identity=_policy(),
    )

    assert observed_paths == [
        impact_path,
    ]


def test_rejects_duplicate_target_paths():
    root = _revision("chunk-a")
    target = _revision("index-a")
    path = LineageImpactPath(
        root=root,
        target=target,
        revisions=(
            root,
            target,
        ),
    )
    impact = LineageImpactAnalysis(
        root=root,
        impacted_revisions=(target,),
        paths=(
            path,
            path,
        ),
    )

    with pytest.raises(
        ValueError,
        match="duplicate target paths",
    ):
        derive_lineage_invalidation_decisions(
            impact=impact,
            policy=lambda path: (
                "invalidate",
                "causal_dependency_changed",
            ),
            policy_identity=_policy(),
        )


def test_rejects_path_set_that_does_not_match_impacted_revisions():
    root = _revision("chunk-a")
    target = _revision("index-a")
    other = _revision("agent-a")

    impact = LineageImpactAnalysis(
        root=root,
        impacted_revisions=(target,),
        paths=(
            LineageImpactPath(
                root=root,
                target=other,
                revisions=(
                    root,
                    other,
                ),
            ),
        ),
    )

    with pytest.raises(
        ValueError,
        match="must match impacted revisions",
    ):
        derive_lineage_invalidation_decisions(
            impact=impact,
            policy=lambda path: (
                "invalidate",
                "causal_dependency_changed",
            ),
            policy_identity=_policy(),
        )


def test_rejects_path_with_different_analysis_root():
    root = _revision("chunk-a")
    other_root = _revision("chunk-b")
    target = _revision("index-a")

    impact = LineageImpactAnalysis(
        root=root,
        impacted_revisions=(target,),
        paths=(
            LineageImpactPath(
                root=other_root,
                target=target,
                revisions=(
                    other_root,
                    target,
                ),
            ),
        ),
    )

    with pytest.raises(
        ValueError,
        match="root must match analysis root",
    ):
        derive_lineage_invalidation_decisions(
            impact=impact,
            policy=lambda path: (
                "invalidate",
                "causal_dependency_changed",
            ),
            policy_identity=_policy(),
        )


def test_invalid_policy_disposition_fails_closed():
    root = _revision("chunk-a")
    target = _revision("index-a")

    impact = LineageImpactAnalysis(
        root=root,
        impacted_revisions=(target,),
        paths=(
            LineageImpactPath(
                root=root,
                target=target,
                revisions=(
                    root,
                    target,
                ),
            ),
        ),
    )

    with pytest.raises(
        ValueError,
        match="Unsupported invalidation disposition",
    ):
        derive_lineage_invalidation_decisions(
            impact=impact,
            policy=lambda path: (
                "delete_everything",
                "invalid_policy",
            ),
            policy_identity=_policy(),
        )