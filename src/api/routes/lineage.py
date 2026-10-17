from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.api.routes.lineage_recovery_context_admission import (
    router as recovery_context_admission_router,
)
from src.api.routes.lineage_recovery_certificate_issuance import (
    router as recovery_certificate_issuance_router,
)
from src.core.security import require_api_key_permission
from src.api.lineage_control_plane_responses import (
    LineageInvalidationControlPlaneResponse,
    LineageInvalidationDecisionResponse,
    LineageInvalidationPolicyResponse,
    LineageRevisionResponse,
    lineage_invalidation_control_plane_response,
    lineage_invalidation_decision_response,
    lineage_revision_response,
    TemporalLineageInvalidationResolutionResponse,
    temporal_lineage_invalidation_resolution_response,
)
from src.domain.lineage_execution_target import (
    LineageExecutionTargetBinding,
    LineageExecutionTargetIdentity,
)
from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
    compare_materialization_dependencies,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.models import ApiKey
from src.models.lineage_invalidation_decision import (
    LineageInvalidationDecisionRecord,
)
from src.services.lineage_impact_service import (
    analyze_lineage_impact,
)
from src.services.lineage_target_integrity_service import (
    LineageTargetIntegrityAssessment,
    assess_lineage_context_integrity,
    assess_lineage_target_integrity,
)
from src.services.lineage_invalidation_control_plane_projection_service import (
    load_lineage_invalidation_control_plane_view,
)
from src.services.lineage_invalidation_execution_verification_service import (
    LineageInvalidationExecutionVerificationConflictError,
    LineageInvalidationExecutionVerificationObservationInvalidError,
    LineageInvalidationExecutionVerificationObservationNotFoundError,
    LineageInvalidationExecutionVerificationResultNotFoundError,
    LineageInvalidationExecutionVerificationResultNotSuccessfulError,
    persist_lineage_invalidation_execution_verification,
)
from src.services.lineage_invalidation_projection_service import (
    PersistedLineageInvalidationDecisionView,
    load_lineage_invalidation_decisions_for_root,
)
from src.services.lineage_invalidation_policy_service import (
    DEFAULT_LINEAGE_INVALIDATION_POLICY,
)
from src.services.lineage_projection_service import (
    load_lineage_observation_by_sequence,
)
from src.services.lineage_observation_persistence_service import (
    LineageObservationConflictError,
    persist_lineage_observation,
)
from src.services.lineage_execution_target_binding_service import (
    LineageExecutionTargetBindingConflictError,
    LineageExecutionTargetBindingRevisionGapError,
    persist_lineage_execution_target_binding,
)
from src.services.temporal_lineage_invalidation_service import (
    TemporalLineageInvalidationEventNotFoundError,
    TemporalLineageInvalidationNotReadyError,
    analyze_temporal_lineage_invalidation,
)
from src.services.temporal_lineage_invalidation_resolution_service import (
    load_temporal_lineage_invalidation_resolution,
)


router = APIRouter(
    prefix="/api/lineage",
    tags=["lineage"],
)


router.include_router(recovery_context_admission_router)
router.include_router(recovery_certificate_issuance_router)

logger = logging.getLogger(__name__)


def _log_integrity_assessment(
    *,
    operation: Literal["target", "context"],
    target_count: int,
    unknown_count: int,
    not_affected_count: int,
    affected_count: int,
    verified_count: int,
    has_unknown: bool,
    has_unresolved_impact: bool,
) -> None:
    logger.info(
        "integrity_assessment_completed",
        extra={
            "operation": operation,
            "target_count": target_count,
            "unknown_count": unknown_count,
            "not_affected_count": not_affected_count,
            "affected_count": affected_count,
            "verified_count": verified_count,
            "has_unknown": has_unknown,
            "has_unresolved_impact": has_unresolved_impact,
        },
    )


class LineageDependencyRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    asset_external_id: str = Field(
        min_length=1,
        max_length=512,
    )
    revision_external_id: str = Field(
        min_length=1,
        max_length=512,
    )
    dependency_type: str = Field(
        default="derived_from",
        min_length=1,
        max_length=128,
    )


class LineageObservationRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    observation_kind: Literal[
        "materialization",
        "consumption",
    ]
    source_external_id: str = Field(
        min_length=1,
        max_length=512,
    )
    snapshot_external_id: str = Field(
        min_length=1,
        max_length=512,
    )
    asset_external_id: str = Field(
        min_length=1,
        max_length=512,
    )
    asset_kind: Literal[
        "chunk",
        "index",
        "agent",
        "workflow",
    ]
    revision_external_id: str = Field(
        min_length=1,
        max_length=512,
    )
    sequence: int = Field(
        ge=0,
    )
    dependencies: list[
        LineageDependencyRequest
    ] = Field(
        default_factory=list,
    )


class LineageObservationResponse(BaseModel):
    id: uuid.UUID
    created: bool


class LineageDependencyTransitionItem(BaseModel):
    asset_external_id: str
    revision_external_id: str
    dependency_type: str


class LineageDependencyTransitionResponse(BaseModel):
    source_external_id: str
    asset_external_id: str
    asset_kind: str
    from_sequence: int
    to_sequence: int
    from_revision_external_id: str
    to_revision_external_id: str
    from_snapshot_external_id: str
    to_snapshot_external_id: str
    added: list[LineageDependencyTransitionItem]
    removed: list[LineageDependencyTransitionItem]
    unchanged: list[LineageDependencyTransitionItem]


class LineageInvalidationExecutionVerificationRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    execution_result_id: uuid.UUID
    observation_id: uuid.UUID


class LineageInvalidationExecutionVerificationPublicationResponse(BaseModel):
    id: uuid.UUID
    execution_result_id: uuid.UUID
    observation_id: uuid.UUID
    created_at: datetime
    created: bool


class LineageExecutionTargetBindingRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    source_external_id: str = Field(
        min_length=1,
        max_length=512,
    )
    asset_external_id: str = Field(
        min_length=1,
        max_length=512,
    )
    binding_revision: int = Field(
        ge=1,
    )
    provider: str = Field(
        min_length=1,
        max_length=128,
    )
    target_kind: str = Field(
        min_length=1,
        max_length=128,
    )
    configuration: dict[str, str]


class LineageExecutionTargetBindingResponse(BaseModel):
    id: uuid.UUID
    source_external_id: str
    asset_external_id: str
    binding_revision: int
    provider: str
    target_kind: str
    configuration: dict[str, str]
    created: bool


class LineageRevisionRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    source_external_id: str = Field(
        min_length=1,
        max_length=512,
    )
    asset_external_id: str = Field(
        min_length=1,
        max_length=512,
    )
    revision_external_id: str = Field(
        min_length=1,
        max_length=512,
    )


class LineageImpactPathResponse(BaseModel):
    root: LineageRevisionResponse
    target: LineageRevisionResponse
    revisions: list[LineageRevisionResponse]


class LineageImpactResponse(BaseModel):
    root: LineageRevisionResponse
    impacted_revisions: list[LineageRevisionResponse]
    paths: list[LineageImpactPathResponse]


class LineageInvalidationDecisionDiscoveryResponse(BaseModel):
    root: LineageRevisionResponse
    decisions: list[LineageInvalidationDecisionResponse]


class LineageTargetIntegrityEvidenceResponse(BaseModel):
    decision_id: uuid.UUID
    root: LineageRevisionResponse
    disposition: Literal[
        "no_action",
        "invalidate",
        "rematerialize",
    ]
    reason_code: str
    state: Literal[
        "no_action",
        "unresolved",
        "verified",
    ]


class LineageTargetIntegrityResponse(BaseModel):
    target: LineageRevisionResponse
    status: Literal[
        "unknown",
        "not_affected",
        "affected",
        "verified",
    ]
    evidence: list[LineageTargetIntegrityEvidenceResponse]


class LineageContextIntegrityRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    targets: list[LineageRevisionRequest] = Field(
        min_length=1,
        max_length=64,
    )


class LineageContextIntegritySummaryResponse(BaseModel):
    target_count: int
    unknown_count: int
    not_affected_count: int
    affected_count: int
    verified_count: int
    has_unknown: bool
    has_unresolved_impact: bool


class LineageContextIntegrityResponse(BaseModel):
    assessments: list[LineageTargetIntegrityResponse]
    summary: LineageContextIntegritySummaryResponse


class TemporalLineageInvalidationAnalysisRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    event_id: uuid.UUID


class TemporalLineageInvalidationRootAnalysisResponse(BaseModel):
    root: LineageRevisionResponse
    decisions: list[LineageInvalidationDecisionResponse]


class TemporalLineageInvalidationAnalysisResponse(BaseModel):
    event_id: uuid.UUID
    old_snapshot_id: uuid.UUID
    new_snapshot_id: uuid.UUID
    analyses: list[
        TemporalLineageInvalidationRootAnalysisResponse
    ]


def _build_observation(
    body: LineageObservationRequest,
) -> LineageObservation:
    try:
        return LineageObservation(
            observation_kind=body.observation_kind,
            source_external_id=body.source_external_id,
            snapshot_external_id=body.snapshot_external_id,
            asset_external_id=body.asset_external_id,
            asset_kind=body.asset_kind,
            revision_external_id=body.revision_external_id,
            sequence=body.sequence,
            dependencies=tuple(
                LineageRevisionReference(
                    asset_external_id=(
                        dependency.asset_external_id
                    ),
                    revision_external_id=(
                        dependency.revision_external_id
                    ),
                    dependency_type=(
                        dependency.dependency_type
                    ),
                )
                for dependency in body.dependencies
            ),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "LINEAGE_001",
                    "message": str(exc),
                    "details": None,
                }
            },
        ) from exc


def _build_execution_target_binding(
    body: LineageExecutionTargetBindingRequest,
) -> LineageExecutionTargetBinding:
    try:
        return LineageExecutionTargetBinding(
            identity=LineageExecutionTargetIdentity(
                source_external_id=body.source_external_id,
                asset_external_id=body.asset_external_id,
            ),
            binding_revision=body.binding_revision,
            provider=body.provider,
            target_kind=body.target_kind,
            configuration=body.configuration,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "LINEAGE_005",
                    "message": str(exc),
                    "details": None,
                }
            },
        ) from exc


def _build_revision_identity(
    body: LineageRevisionRequest,
) -> LineageRevisionIdentity:
    try:
        return LineageRevisionIdentity(
            source_external_id=body.source_external_id,
            asset_external_id=body.asset_external_id,
            revision_external_id=body.revision_external_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "LINEAGE_003",
                    "message": str(exc),
                    "details": None,
                }
            },
        ) from exc


def _persisted_invalidation_decision_response(
    record: LineageInvalidationDecisionRecord,
) -> LineageInvalidationDecisionResponse:
    causal_path = sorted(
        record.causal_path,
        key=lambda path_revision: path_revision.ordinal,
    )

    return LineageInvalidationDecisionResponse(
        id=record.id,
        root=LineageRevisionResponse(
            source_external_id=record.root_source_external_id,
            asset_external_id=record.root_asset_external_id,
            revision_external_id=record.root_revision_external_id,
        ),
        target=LineageRevisionResponse(
            source_external_id=record.target_source_external_id,
            asset_external_id=record.target_asset_external_id,
            revision_external_id=record.target_revision_external_id,
        ),
        policy=LineageInvalidationPolicyResponse(
            policy_external_id=record.policy_external_id,
            revision=record.policy_revision,
        ),
        disposition=record.disposition,
        reason_code=record.reason_code,
        causal_path=[
            LineageRevisionResponse(
                source_external_id=path_revision.source_external_id,
                asset_external_id=path_revision.asset_external_id,
                revision_external_id=path_revision.revision_external_id,
            )
            for path_revision in causal_path
        ],
    )


@router.post(
    "/impact",
    response_model=LineageImpactResponse,
    status_code=status.HTTP_200_OK,
)
def analyze_lineage_revision_impact(
    body: LineageRevisionRequest,
    api_key: ApiKey = Depends(
        require_api_key_permission(
            "read"
        )
    ),
    db: Session = Depends(
        get_db
    ),
) -> LineageImpactResponse:
    root = _build_revision_identity(
        body
    )

    analysis = analyze_lineage_impact(
        db,
        organization_id=(
            api_key.organization_id
        ),
        root=root,
    )

    return LineageImpactResponse(
        root=lineage_revision_response(
            analysis.root
        ),
        impacted_revisions=[
            lineage_revision_response(revision)
            for revision
            in analysis.impacted_revisions
        ],
        paths=[
            LineageImpactPathResponse(
                root=lineage_revision_response(
                    path.root
                ),
                target=lineage_revision_response(
                    path.target
                ),
                revisions=[
                    lineage_revision_response(revision)
                    for revision
                    in path.revisions
                ],
            )
            for path in analysis.paths
        ],
    )


@router.post(
    "/invalidation-decisions/query",
    response_model=LineageInvalidationDecisionDiscoveryResponse,
    status_code=status.HTTP_200_OK,
)
def query_lineage_invalidation_decisions(
    body: LineageRevisionRequest,
    api_key: ApiKey = Depends(
        require_api_key_permission(
            "read"
        )
    ),
    db: Session = Depends(
        get_db
    ),
) -> LineageInvalidationDecisionDiscoveryResponse:
    root = _build_revision_identity(
        body
    )

    decisions = load_lineage_invalidation_decisions_for_root(
        db,
        organization_id=api_key.organization_id,
        root=root,
    )

    return LineageInvalidationDecisionDiscoveryResponse(
        root=lineage_revision_response(root),
        decisions=[
            lineage_invalidation_decision_response(view)
            for view in decisions
        ],
    )


def _target_integrity_response(
    assessment: LineageTargetIntegrityAssessment,
) -> LineageTargetIntegrityResponse:
    return LineageTargetIntegrityResponse(
        target=lineage_revision_response(assessment.target),
        status=assessment.status,
        evidence=[
            LineageTargetIntegrityEvidenceResponse(
                decision_id=item.decision_id,
                root=lineage_revision_response(item.root),
                disposition=item.disposition,
                reason_code=item.reason_code,
                state=item.state,
            )
            for item in assessment.evidence
        ],
    )


@router.post(
    "/target-integrity/query",
    response_model=LineageTargetIntegrityResponse,
    status_code=status.HTTP_200_OK,
)
def query_lineage_target_integrity(
    body: LineageRevisionRequest,
    api_key: ApiKey = Depends(
        require_api_key_permission(
            "read"
        )
    ),
    db: Session = Depends(
        get_db
    ),
) -> LineageTargetIntegrityResponse:
    target = _build_revision_identity(body)
    assessment = assess_lineage_target_integrity(
        db,
        organization_id=api_key.organization_id,
        target=target,
    )

    _log_integrity_assessment(
        operation="target",
        target_count=1,
        unknown_count=int(assessment.status == "unknown"),
        not_affected_count=int(assessment.status == "not_affected"),
        affected_count=int(assessment.status == "affected"),
        verified_count=int(assessment.status == "verified"),
        has_unknown=assessment.status == "unknown",
        has_unresolved_impact=assessment.status == "affected",
    )

    return _target_integrity_response(assessment)


@router.post(
    "/context-integrity/query",
    response_model=LineageContextIntegrityResponse,
    status_code=status.HTTP_200_OK,
)
def query_lineage_context_integrity(
    body: LineageContextIntegrityRequest,
    api_key: ApiKey = Depends(
        require_api_key_permission(
            "read"
        )
    ),
    db: Session = Depends(
        get_db
    ),
) -> LineageContextIntegrityResponse:
    targets = tuple(
        _build_revision_identity(target)
        for target in body.targets
    )
    assessment = assess_lineage_context_integrity(
        db,
        organization_id=api_key.organization_id,
        targets=targets,
    )

    _log_integrity_assessment(
        operation="context",
        target_count=assessment.summary.target_count,
        unknown_count=assessment.summary.unknown_count,
        not_affected_count=assessment.summary.not_affected_count,
        affected_count=assessment.summary.affected_count,
        verified_count=assessment.summary.verified_count,
        has_unknown=assessment.summary.has_unknown,
        has_unresolved_impact=assessment.summary.has_unresolved_impact,
    )

    return LineageContextIntegrityResponse(
        assessments=[
            _target_integrity_response(target_assessment)
            for target_assessment in assessment.assessments
        ],
        summary=LineageContextIntegritySummaryResponse(
            target_count=assessment.summary.target_count,
            unknown_count=assessment.summary.unknown_count,
            not_affected_count=(
                assessment.summary.not_affected_count
            ),
            affected_count=assessment.summary.affected_count,
            verified_count=assessment.summary.verified_count,
            has_unknown=assessment.summary.has_unknown,
            has_unresolved_impact=(
                assessment.summary.has_unresolved_impact
            ),
        ),
    )


@router.get(
    "/invalidation-decisions/{decision_id}",
    response_model=LineageInvalidationControlPlaneResponse,
    status_code=status.HTTP_200_OK,
)
def get_lineage_invalidation_decision(
    decision_id: uuid.UUID,
    api_key: ApiKey = Depends(
        require_api_key_permission(
            "read"
        )
    ),
    db: Session = Depends(
        get_db
    ),
) -> LineageInvalidationControlPlaneResponse:
    view = load_lineage_invalidation_control_plane_view(
        db,
        organization_id=api_key.organization_id,
        decision_record_id=decision_id,
    )

    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "LINEAGE_004",
                    "message": "Lineage invalidation decision not found.",
                    "details": None,
                }
            },
        )

    return lineage_invalidation_control_plane_response(view)


@router.post(
    "/temporal-invalidation-analysis",
    response_model=TemporalLineageInvalidationAnalysisResponse,
    status_code=status.HTTP_200_OK,
)
def analyze_temporal_lineage_invalidation_event(
    body: TemporalLineageInvalidationAnalysisRequest,
    api_key: ApiKey = Depends(
        require_api_key_permission(
            "write"
        )
    ),
    db: Session = Depends(
        get_db
    ),
) -> TemporalLineageInvalidationAnalysisResponse:
    try:
        analysis = analyze_temporal_lineage_invalidation(
            db,
            organization_id=api_key.organization_id,
            event_id=body.event_id,
            policy_identity=DEFAULT_LINEAGE_INVALIDATION_POLICY,
        )
        db.commit()
    except TemporalLineageInvalidationEventNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "LINEAGE_008",
                    "message": "Temporal drift event not found.",
                    "details": None,
                }
            },
        ) from exc
    except TemporalLineageInvalidationNotReadyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "LINEAGE_009",
                    "message": str(exc),
                    "details": None,
                }
            },
        ) from exc
    except Exception:
        db.rollback()
        raise

    return TemporalLineageInvalidationAnalysisResponse(
        event_id=analysis.event_id,
        old_snapshot_id=analysis.old_snapshot_id,
        new_snapshot_id=analysis.new_snapshot_id,
        analyses=[
            TemporalLineageInvalidationRootAnalysisResponse(
                root=lineage_revision_response(root),
                decisions=[
                    _persisted_invalidation_decision_response(
                        persisted.record
                    )
                    for persisted in root_analysis.decisions
                ],
            )
            for root, root_analysis in zip(
                analysis.roots,
                analysis.analyses,
                strict=True,
            )
        ],
    )


@router.get(
    "/temporal-invalidation-resolution/{event_id}",
    response_model=TemporalLineageInvalidationResolutionResponse,
    status_code=status.HTTP_200_OK,
)
def get_temporal_lineage_invalidation_resolution(
    event_id: uuid.UUID,
    api_key: ApiKey = Depends(
        require_api_key_permission(
            "read"
        )
    ),
    db: Session = Depends(
        get_db
    ),
) -> TemporalLineageInvalidationResolutionResponse:
    try:
        resolution = load_temporal_lineage_invalidation_resolution(
            db,
            organization_id=api_key.organization_id,
            event_id=event_id,
        )
    except TemporalLineageInvalidationEventNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "LINEAGE_008",
                    "message": "Temporal drift event not found.",
                    "details": None,
                }
            },
        ) from exc

    return temporal_lineage_invalidation_resolution_response(
        resolution
    )


@router.post(
    "/execution-target-bindings",
    response_model=LineageExecutionTargetBindingResponse,
    status_code=status.HTTP_200_OK,
)
def publish_lineage_execution_target_binding(
    body: LineageExecutionTargetBindingRequest,
    api_key: ApiKey = Depends(
        require_api_key_permission(
            "write"
        )
    ),
    db: Session = Depends(
        get_db
    ),
) -> LineageExecutionTargetBindingResponse:
    binding = _build_execution_target_binding(
        body
    )

    try:
        persisted = persist_lineage_execution_target_binding(
            db,
            organization_id=api_key.organization_id,
            binding=binding,
        )
        db.commit()
    except LineageExecutionTargetBindingConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "LINEAGE_006",
                    "message": (
                        "A conflicting execution target binding "
                        "already exists for this target revision."
                    ),
                    "details": None,
                }
            },
        ) from exc
    except LineageExecutionTargetBindingRevisionGapError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "LINEAGE_007",
                    "message": str(exc),
                    "details": None,
                }
            },
        ) from exc
    except Exception:
        db.rollback()
        raise

    record = persisted.record

    return LineageExecutionTargetBindingResponse(
        id=record.id,
        source_external_id=record.source_external_id,
        asset_external_id=record.asset_external_id,
        binding_revision=record.binding_revision,
        provider=record.provider,
        target_kind=record.target_kind,
        configuration=dict(record.configuration),
        created=persisted.created,
    )


@router.get(
    "/materialization-dependency-diff",
    response_model=LineageDependencyTransitionResponse,
    status_code=status.HTTP_200_OK,
)
def get_materialization_dependency_diff(
    source_external_id: str = Query(min_length=1, max_length=512),
    asset_external_id: str = Query(min_length=1, max_length=512),
    from_sequence: int = Query(ge=0),
    to_sequence: int = Query(ge=0),
    api_key: ApiKey = Depends(require_api_key_permission("read")),
    db: Session = Depends(get_db),
) -> LineageDependencyTransitionResponse:
    """Explain recorded dependency additions/removals across revisions.

    A removed dependency is absent from the later materialization report;
    it does not constitute proof that an external real-world relationship
    stopped existing outside this particular lineage subject.
    """
    if from_sequence >= to_sequence:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from_sequence must be earlier than to_sequence.",
        )

    identity = dict(
        organization_id=api_key.organization_id,
        source_external_id=source_external_id,
        asset_external_id=asset_external_id,
        observation_kind="materialization",
    )
    before = load_lineage_observation_by_sequence(
        db, **identity, sequence=from_sequence,
    )
    after = load_lineage_observation_by_sequence(
        db, **identity, sequence=to_sequence,
    )
    if before is None or after is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Requested lineage materialization sequence not found.",
        )

    transition = compare_materialization_dependencies(before, after)

    def items(dependencies: tuple[LineageRevisionReference, ...]):
        return [
            LineageDependencyTransitionItem(
                asset_external_id=dep.asset_external_id,
                revision_external_id=dep.revision_external_id,
                dependency_type=dep.dependency_type,
            )
            for dep in dependencies
        ]

    return LineageDependencyTransitionResponse(
        source_external_id=before.source_external_id,
        asset_external_id=before.asset_external_id,
        asset_kind=before.asset_kind,
        from_sequence=before.sequence,
        to_sequence=after.sequence,
        from_revision_external_id=before.revision_external_id,
        to_revision_external_id=after.revision_external_id,
        from_snapshot_external_id=before.snapshot_external_id,
        to_snapshot_external_id=after.snapshot_external_id,
        added=items(transition.added),
        removed=items(transition.removed),
        unchanged=items(transition.unchanged),
    )


@router.post(
    "/observations",
    response_model=LineageObservationResponse,
    status_code=status.HTTP_200_OK,
)
def ingest_lineage_observation(
    body: LineageObservationRequest,
    api_key: ApiKey = Depends(
        require_api_key_permission(
            "write"
        )
    ),
    db: Session = Depends(
        get_db
    ),
) -> LineageObservationResponse:
    observation = _build_observation(
        body
    )

    try:
        persisted = persist_lineage_observation(
            db,
            organization_id=(
                api_key.organization_id
            ),
            observation=observation,
        )
        db.commit()
    except LineageObservationConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "LINEAGE_002",
                    "message": (
                        "A conflicting lineage observation "
                        "already exists for this subject sequence."
                    ),
                    "details": None,
                }
            },
        ) from exc
    except Exception:
        db.rollback()
        raise

    return LineageObservationResponse(
        id=persisted.record.id,
        created=persisted.created,
    )


@router.post(
    "/invalidation-execution-verifications",
    response_model=(
        LineageInvalidationExecutionVerificationPublicationResponse
    ),
    status_code=status.HTTP_200_OK,
)
def publish_lineage_invalidation_execution_verification(
    body: LineageInvalidationExecutionVerificationRequest,
    api_key: ApiKey = Depends(
        require_api_key_permission(
            "write"
        )
    ),
    db: Session = Depends(
        get_db
    ),
) -> LineageInvalidationExecutionVerificationPublicationResponse:
    try:
        persisted = (
            persist_lineage_invalidation_execution_verification(
                db,
                organization_id=api_key.organization_id,
                execution_result_id=body.execution_result_id,
                observation_id=body.observation_id,
            )
        )
        db.commit()
    except (
        LineageInvalidationExecutionVerificationResultNotFoundError,
        LineageInvalidationExecutionVerificationObservationNotFoundError,
    ) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "LINEAGE_010",
                    "message": str(exc),
                    "details": None,
                }
            },
        ) from exc
    except (
        LineageInvalidationExecutionVerificationResultNotSuccessfulError,
        LineageInvalidationExecutionVerificationObservationInvalidError,
    ) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "LINEAGE_011",
                    "message": str(exc),
                    "details": None,
                }
            },
        ) from exc
    except LineageInvalidationExecutionVerificationConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "LINEAGE_012",
                    "message": str(exc),
                    "details": {
                        "existing_observation_id": str(
                            exc.existing_observation_id
                        ),
                        "requested_observation_id": str(
                            exc.requested_observation_id
                        ),
                    },
                }
            },
        ) from exc
    except Exception:
        db.rollback()
        raise

    record = persisted.record

    return (
        LineageInvalidationExecutionVerificationPublicationResponse(
            id=record.id,
            execution_result_id=record.execution_result_id,
            observation_id=record.observation_id,
            created_at=record.created_at,
            created=persisted.created,
        )
    )