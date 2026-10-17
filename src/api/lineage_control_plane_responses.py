from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.services.lineage_invalidation_control_plane_projection_service import (
    LineageInvalidationControlPlaneView,
)
from src.services.lineage_invalidation_projection_service import (
    PersistedLineageInvalidationDecisionView,
)


class LineageRevisionResponse(BaseModel):
    source_external_id: str
    asset_external_id: str
    revision_external_id: str


class LineageInvalidationPolicyResponse(BaseModel):
    policy_external_id: str
    revision: int


class LineageInvalidationDecisionResponse(BaseModel):
    id: uuid.UUID
    root: LineageRevisionResponse
    target: LineageRevisionResponse
    policy: LineageInvalidationPolicyResponse
    disposition: Literal[
        "no_action",
        "invalidate",
        "rematerialize",
    ]
    reason_code: str
    causal_path: list[LineageRevisionResponse]


class LineageInvalidationEligibilityResponse(BaseModel):
    eligible: bool
    reason_code: Literal[
        "eligible",
        "no_action",
        "policy_not_active",
        "policy_revision_superseded",
        "policy_revision_not_active",
        "target_authority_missing",
        "target_not_current",
    ]


class LineageInvalidationExecutionIntentResponse(BaseModel):
    id: uuid.UUID
    authorized_at: datetime


class LineageInvalidationExecutionAttemptResponse(BaseModel):
    id: uuid.UUID
    attempt_number: int
    binding_revision: int
    claimed_at: datetime
    lease_expires_at: datetime


class LineageInvalidationExecutionResultResponse(BaseModel):
    id: uuid.UUID
    attempt_id: uuid.UUID
    outcome: Literal[
        "succeeded",
        "failed",
    ]
    recorded_at: datetime


class LineageInvalidationExecutionVerificationResponse(BaseModel):
    state: Literal[
        "awaiting_evidence",
        "stale_dependency_present",
        "verified",
    ]
    execution_result_id: uuid.UUID
    verification_record_id: uuid.UUID | None
    observation_id: uuid.UUID | None
    stale_dependency: LineageRevisionResponse


class LineageInvalidationExecutionResponse(BaseModel):
    intent: LineageInvalidationExecutionIntentResponse | None
    latest_attempt: LineageInvalidationExecutionAttemptResponse | None
    result: LineageInvalidationExecutionResultResponse | None
    verification: (
        LineageInvalidationExecutionVerificationResponse | None
    )


LineageInvalidationControlState = Literal[
    "historical_no_action",
    "blocked_current_state",
    "eligible_awaiting_authority",
    "authorized_awaiting_execution",
    "execution_attempt_recorded",
    "execution_failed",
    "execution_succeeded_unverified",
    "awaiting_evidence",
    "stale_dependency_present",
    "verified",
]


class LineageInvalidationControlPlaneResponse(BaseModel):
    decision: LineageInvalidationDecisionResponse
    control_state: LineageInvalidationControlState
    eligibility: LineageInvalidationEligibilityResponse
    execution: LineageInvalidationExecutionResponse


def lineage_revision_response(
    revision: LineageRevisionIdentity,
) -> LineageRevisionResponse:
    return LineageRevisionResponse(
        source_external_id=revision.source_external_id,
        asset_external_id=revision.asset_external_id,
        revision_external_id=revision.revision_external_id,
    )


def lineage_invalidation_decision_response(
    view: PersistedLineageInvalidationDecisionView,
) -> LineageInvalidationDecisionResponse:
    decision = view.decision

    return LineageInvalidationDecisionResponse(
        id=view.record_id,
        root=lineage_revision_response(decision.root),
        target=lineage_revision_response(decision.target),
        policy=LineageInvalidationPolicyResponse(
            policy_external_id=decision.policy.policy_external_id,
            revision=decision.policy.revision,
        ),
        disposition=decision.disposition,
        reason_code=decision.reason_code,
        causal_path=[
            lineage_revision_response(revision)
            for revision in decision.causal_path
        ],
    )


def _lineage_invalidation_control_state(
    view: LineageInvalidationControlPlaneView,
) -> LineageInvalidationControlState:
    verification = view.execution.verification
    if verification is not None:
        if verification.state == "verified":
            return "verified"
        if verification.state == "stale_dependency_present":
            return "stale_dependency_present"
        return "awaiting_evidence"

    result = view.execution.result
    if result is not None:
        if result.outcome == "failed":
            return "execution_failed"
        return "execution_succeeded_unverified"

    if view.execution.latest_attempt is not None:
        return "execution_attempt_recorded"

    if view.execution.intent is not None:
        return "authorized_awaiting_execution"

    if view.eligibility.eligible:
        return "eligible_awaiting_authority"

    if view.decision.decision.disposition == "no_action":
        return "historical_no_action"

    return "blocked_current_state"


def lineage_invalidation_control_plane_response(
    view: LineageInvalidationControlPlaneView,
) -> LineageInvalidationControlPlaneResponse:
    intent = None
    if view.execution.intent is not None:
        intent = LineageInvalidationExecutionIntentResponse(
            id=view.execution.intent.record_id,
            authorized_at=view.execution.intent.authorized_at,
        )

    latest_attempt = None
    if view.execution.latest_attempt is not None:
        latest_attempt = LineageInvalidationExecutionAttemptResponse(
            id=view.execution.latest_attempt.record_id,
            attempt_number=view.execution.latest_attempt.attempt_number,
            binding_revision=view.execution.latest_attempt.binding_revision,
            claimed_at=view.execution.latest_attempt.claimed_at,
            lease_expires_at=view.execution.latest_attempt.lease_expires_at,
        )

    result = None
    if view.execution.result is not None:
        result = LineageInvalidationExecutionResultResponse(
            id=view.execution.result.record_id,
            attempt_id=view.execution.result.attempt_id,
            outcome=view.execution.result.outcome,
            recorded_at=view.execution.result.recorded_at,
        )

    verification = None
    if view.execution.verification is not None:
        verification = LineageInvalidationExecutionVerificationResponse(
            state=view.execution.verification.state,
            execution_result_id=(
                view.execution.verification.execution_result_id
            ),
            verification_record_id=(
                view.execution.verification.verification_record_id
            ),
            observation_id=(
                view.execution.verification.observation_id
            ),
            stale_dependency=LineageRevisionResponse(
                source_external_id=(
                    view.execution.verification
                    .stale_dependency.source_external_id
                ),
                asset_external_id=(
                    view.execution.verification
                    .stale_dependency.asset_external_id
                ),
                revision_external_id=(
                    view.execution.verification
                    .stale_dependency.revision_external_id
                ),
            ),
        )

    return LineageInvalidationControlPlaneResponse(
        decision=lineage_invalidation_decision_response(
            view.decision
        ),
        control_state=_lineage_invalidation_control_state(view),
        eligibility=LineageInvalidationEligibilityResponse(
            eligible=view.eligibility.eligible,
            reason_code=view.eligibility.reason_code,
        ),
        execution=LineageInvalidationExecutionResponse(
            intent=intent,
            latest_attempt=latest_attempt,
            result=result,
            verification=verification,
        ),
    )


class TemporalLineageHistoricalImpactSummaryResponse(BaseModel):
    root_count: int
    target_count: int
    no_action_count: int
    invalidate_count: int
    rematerialize_count: int


class TemporalLineageCurrentControlSummaryResponse(BaseModel):
    eligible_count: int
    authorized_count: int
    attempted_count: int
    succeeded_count: int
    failed_count: int
    awaiting_evidence_count: int
    stale_dependency_present_count: int
    verified_count: int


class TemporalLineageControlStateDistributionResponse(BaseModel):
    historical_no_action_count: int
    blocked_current_state_count: int
    eligible_awaiting_authority_count: int
    authorized_awaiting_execution_count: int
    execution_attempt_recorded_count: int
    execution_failed_count: int
    execution_succeeded_unverified_count: int
    awaiting_evidence_count: int
    stale_dependency_present_count: int
    verified_count: int


class TemporalLineageIntegritySummaryResponse(BaseModel):
    historical_impact: TemporalLineageHistoricalImpactSummaryResponse
    current_control: TemporalLineageCurrentControlSummaryResponse
    control_state_distribution: (
        TemporalLineageControlStateDistributionResponse
    )


class TemporalLineageInvalidationResolutionResponse(BaseModel):
    event_id: uuid.UUID
    old_snapshot_id: uuid.UUID
    new_snapshot_id: uuid.UUID
    summary: TemporalLineageIntegritySummaryResponse
    decisions: list[
        LineageInvalidationControlPlaneResponse
    ]


def _temporal_lineage_integrity_summary(
    resolution,
) -> TemporalLineageIntegritySummaryResponse:
    decisions = resolution.decisions
    roots = {view.decision.decision.root for view in decisions}
    targets = {view.decision.decision.target for view in decisions}
    control_states = [
        _lineage_invalidation_control_state(view)
        for view in decisions
    ]

    return TemporalLineageIntegritySummaryResponse(
        historical_impact=TemporalLineageHistoricalImpactSummaryResponse(
            root_count=len(roots),
            target_count=len(targets),
            no_action_count=sum(
                view.decision.decision.disposition == "no_action"
                for view in decisions
            ),
            invalidate_count=sum(
                view.decision.decision.disposition == "invalidate"
                for view in decisions
            ),
            rematerialize_count=sum(
                view.decision.decision.disposition == "rematerialize"
                for view in decisions
            ),
        ),
        current_control=TemporalLineageCurrentControlSummaryResponse(
            eligible_count=sum(
                view.eligibility.eligible
                for view in decisions
            ),
            authorized_count=sum(
                view.execution.intent is not None
                for view in decisions
            ),
            attempted_count=sum(
                view.execution.latest_attempt is not None
                for view in decisions
            ),
            succeeded_count=sum(
                view.execution.result is not None
                and view.execution.result.outcome == "succeeded"
                for view in decisions
            ),
            failed_count=sum(
                view.execution.result is not None
                and view.execution.result.outcome == "failed"
                for view in decisions
            ),
            awaiting_evidence_count=sum(
                view.execution.verification is not None
                and view.execution.verification.state == "awaiting_evidence"
                for view in decisions
            ),
            stale_dependency_present_count=sum(
                view.execution.verification is not None
                and view.execution.verification.state
                == "stale_dependency_present"
                for view in decisions
            ),
            verified_count=sum(
                view.execution.verification is not None
                and view.execution.verification.state == "verified"
                for view in decisions
            ),
        ),
        control_state_distribution=(
            TemporalLineageControlStateDistributionResponse(
                historical_no_action_count=control_states.count("historical_no_action"),
                blocked_current_state_count=control_states.count("blocked_current_state"),
                eligible_awaiting_authority_count=control_states.count("eligible_awaiting_authority"),
                authorized_awaiting_execution_count=control_states.count("authorized_awaiting_execution"),
                execution_attempt_recorded_count=control_states.count("execution_attempt_recorded"),
                execution_failed_count=control_states.count("execution_failed"),
                execution_succeeded_unverified_count=control_states.count("execution_succeeded_unverified"),
                awaiting_evidence_count=control_states.count("awaiting_evidence"),
                stale_dependency_present_count=control_states.count("stale_dependency_present"),
                verified_count=control_states.count("verified"),
            )
        ),
    )


def temporal_lineage_invalidation_resolution_response(
    resolution,
) -> TemporalLineageInvalidationResolutionResponse:
    return TemporalLineageInvalidationResolutionResponse(
        event_id=resolution.event_id,
        old_snapshot_id=resolution.old_snapshot_id,
        new_snapshot_id=resolution.new_snapshot_id,
        summary=_temporal_lineage_integrity_summary(resolution),
        decisions=[
            lineage_invalidation_control_plane_response(view)
            for view in resolution.decisions
        ],
    )
