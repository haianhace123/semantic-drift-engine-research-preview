from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import uuid

from sqlalchemy.orm import Session

from src.domain.lineage_invalidation import (
    LineageInvalidationDecision,
    LineageInvalidationDisposition,
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_observation import (
    LineageAssetKind,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.services.lineage_impact_service import (
    LineageImpactAnalysis,
    LineageImpactPath,
    analyze_lineage_impact,
)
from src.services.lineage_invalidation_persistence_service import (
    PersistedLineageInvalidationDecision,
    persist_lineage_invalidation_decision,
)
from src.services.lineage_invalidation_policy_service import (
    evaluate_lineage_invalidation_policy,
)
from src.services.lineage_projection_service import (
    load_lineage_revision,
)


LineageInvalidationPolicy = Callable[
    [LineageImpactPath],
    tuple[
        LineageInvalidationDisposition,
        str,
    ],
]


class LineageInvalidationTargetFactsNotFoundError(RuntimeError):
    """Raised when an impacted exact revision has no recorded lineage facts."""


class LineageInvalidationTargetFactsConflictError(RuntimeError):
    """Raised when exact revision facts disagree on authoritative asset kind."""


@dataclass(frozen=True)
class PersistedLineageInvalidationAnalysis:
    impact: LineageImpactAnalysis
    decisions: tuple[
        PersistedLineageInvalidationDecision,
        ...,
    ]


def analyze_and_persist_lineage_invalidation_decisions(
    db: Session,
    *,
    organization_id: uuid.UUID,
    root: LineageRevisionIdentity,
    policy_identity: LineageInvalidationPolicyIdentity,
) -> PersistedLineageInvalidationAnalysis:
    impact = analyze_lineage_impact(
        db,
        organization_id=organization_id,
        root=root,
    )

    asset_kinds_by_target = {
        target: _load_authoritative_target_asset_kind(
            db,
            organization_id=organization_id,
            target=target,
        )
        for target in impact.impacted_revisions
    }

    decisions = derive_lineage_invalidation_decisions(
        impact=impact,
        policy_identity=policy_identity,
        policy=lambda path: _evaluate_target_policy(
            policy_identity=policy_identity,
            target_asset_kind=asset_kinds_by_target[path.target],
        ),
    )

    persisted = tuple(
        persist_lineage_invalidation_decision(
            db,
            organization_id=organization_id,
            decision=decision,
        )
        for decision in decisions
    )

    return PersistedLineageInvalidationAnalysis(
        impact=impact,
        decisions=persisted,
    )


def derive_lineage_invalidation_decisions(
    *,
    impact: LineageImpactAnalysis,
    policy_identity: LineageInvalidationPolicyIdentity,
    policy: LineageInvalidationPolicy,
) -> tuple[LineageInvalidationDecision, ...]:
    paths_by_target = {
        path.target: path
        for path in impact.paths
    }

    if len(paths_by_target) != len(
        impact.paths
    ):
        raise ValueError(
            "Impact analysis contains duplicate target paths."
        )

    if set(paths_by_target) != set(
        impact.impacted_revisions
    ):
        raise ValueError(
            "Impact analysis paths must match impacted revisions."
        )

    decisions = []

    for target in impact.impacted_revisions:
        path = paths_by_target[target]

        if path.root != impact.root:
            raise ValueError(
                "Impact path root must match analysis root."
            )

        disposition, reason_code = policy(path)

        decisions.append(
            LineageInvalidationDecision(
                root=impact.root,
                target=target,
                policy=policy_identity,
                disposition=disposition,
                reason_code=reason_code,
                causal_path=path.revisions,
            )
        )

    return tuple(decisions)


def _load_authoritative_target_asset_kind(
    db: Session,
    *,
    organization_id: uuid.UUID,
    target: LineageRevisionIdentity,
) -> LineageAssetKind:
    observations = load_lineage_revision(
        db,
        organization_id=organization_id,
        identity=target,
    )

    if not observations:
        raise LineageInvalidationTargetFactsNotFoundError(
            "Impacted exact revision has no recorded lineage facts."
        )

    asset_kinds = {
        observation.asset_kind
        for observation in observations
    }

    if len(asset_kinds) != 1:
        raise LineageInvalidationTargetFactsConflictError(
            "Impacted exact revision has conflicting asset kinds."
        )

    return next(iter(asset_kinds))


def _evaluate_target_policy(
    *,
    policy_identity: LineageInvalidationPolicyIdentity,
    target_asset_kind: LineageAssetKind,
) -> tuple[
    LineageInvalidationDisposition,
    str,
]:
    evaluation = evaluate_lineage_invalidation_policy(
        policy=policy_identity,
        target_asset_kind=target_asset_kind,
    )

    return (
        evaluation.disposition,
        evaluation.reason_code,
    )
