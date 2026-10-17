from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, exists, func, or_
from sqlalchemy.orm import Session

from src.models.organization_alert_policy_revision import (
    OrganizationAlertPolicyRevision,
)
from src.models.organization_work_schedule import (
    WORK_KIND_ALERT_RECONCILIATION,
    WORK_KIND_INTELLIGENCE,
    OrganizationWorkSchedule,
)
from src.models.pipeline_checkpoint import (
    PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1,
    PIPELINE_ALERT_GENERATION_V1,
    PipelineCheckpoint,
)
from src.models.risk_assessment import RiskAssessment
from src.models.webhook_subscription import WebhookSubscription
from src.services.pipeline_checkpoint_service import (
    load_pipeline_checkpoint,
    source_watermark_is_newer,
)
from src.services.risk_assessment_service import (
    RISK_VERSION_V2,
)


class PipelineSourceDiscoveryValidationError(
    ValueError
):
    """Raised when pipeline source discovery input is invalid."""


@dataclass(frozen=True)
class PipelineSourceDiscovery:
    """
    Pending organization-level intelligence source position.

    source_created_at / source_record_id:
        Lexicographic materialization watermark from the newest
        visible deterministic Risk v2 assessment.

    temporal_cursor:
        Monotonic effective-time cursor used by downstream
        Pattern/Anomaly detectors.

    A newly materialized backfill may advance the source watermark
    while carrying an older assessed_at. temporal_cursor therefore
    never moves backward relative to the existing pipeline checkpoint.
    """

    organization_id: uuid.UUID
    pipeline_name: str
    source_created_at: datetime
    source_record_id: uuid.UUID
    temporal_cursor: datetime


def _as_uuid(
    value,
    *,
    field_name: str,
) -> uuid.UUID:
    if isinstance(
        value,
        uuid.UUID,
    ):
        return value

    try:
        return uuid.UUID(
            str(value)
        )
    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        raise (
            PipelineSourceDiscoveryValidationError(
                f"{field_name} must be a valid UUID."
            )
        ) from exc


def _latest_source_row(
    db: Session,
    *,
    organization_id: uuid.UUID,
):
    """
    Return the newest deterministic Risk v2 source row using the
    stable lexicographic materialization ordering:

        created_at DESC,
        id DESC
    """

    return (
        db.query(
            RiskAssessment.id,
            RiskAssessment.created_at,
        )
        .filter(
            RiskAssessment.organization_id
            == organization_id,
            RiskAssessment.risk_version
            == RISK_VERSION_V2,
        )
        .order_by(
            RiskAssessment.created_at.desc(),
            RiskAssessment.id.desc(),
        )
        .first()
    )


def _max_visible_assessed_at(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_created_at: datetime,
    source_record_id: uuid.UUID,
) -> datetime | None:
    """
    Return the greatest effective-time cursor visible through the
    supplied source watermark.

    The tuple boundary is explicit even though source_created_at is
    normally the newest materialization timestamp. This keeps source
    discovery deterministic when multiple rows share the same
    created_at value.
    """

    source_boundary = or_(
        RiskAssessment.created_at
        < source_created_at,
        and_(
            RiskAssessment.created_at
            == source_created_at,
            RiskAssessment.id
            <= source_record_id,
        ),
    )

    return (
        db.query(
            func.max(
                RiskAssessment.assessed_at
            )
        )
        .filter(
            RiskAssessment.organization_id
            == organization_id,
            RiskAssessment.risk_version
            == RISK_VERSION_V2,
            source_boundary,
        )
        .scalar()
    )


def _build_alert_reconciliation_source(
    db: Session,
    *,
    organization_id: uuid.UUID,
) -> PipelineSourceDiscovery | None:
    """
    Build the latest durable Alert reconciliation source for one organization.

    Action-plane reconciliation never fabricates a knowledge source. The
    organization must already have deterministic Risk v2 evidence.

    The Alert generation checkpoint participates only in temporal cursor
    monotonicity. It does not determine whether reconciliation is pending.
    """

    latest = _latest_source_row(
        db,
        organization_id=organization_id,
    )

    if latest is None:
        return None

    source_record_id = latest[0]
    source_created_at = latest[1]

    max_assessed_at = _max_visible_assessed_at(
        db,
        organization_id=organization_id,
        source_created_at=source_created_at,
        source_record_id=source_record_id,
    )

    if max_assessed_at is None:
        raise RuntimeError(
            "A Risk v2 source watermark exists but no "
            "visible assessed_at value could be resolved."
        )

    alert_checkpoint = load_pipeline_checkpoint(
        db,
        organization_id=organization_id,
        pipeline_name=PIPELINE_ALERT_GENERATION_V1,
    )

    temporal_cursor = max_assessed_at

    if (
        alert_checkpoint is not None
        and alert_checkpoint.knowledge_cursor
        > temporal_cursor
    ):
        temporal_cursor = (
            alert_checkpoint.knowledge_cursor
        )

    return PipelineSourceDiscovery(
        organization_id=organization_id,
        pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        source_created_at=source_created_at,
        source_record_id=source_record_id,
        temporal_cursor=temporal_cursor,
    )


def _policy_reconciliation_is_pending(
    db: Session,
    *,
    organization_id: uuid.UUID,
) -> bool:
    return (
        db.query(
            OrganizationAlertPolicyRevision.organization_id
        )
        .filter(
            OrganizationAlertPolicyRevision.organization_id
            == organization_id,
            OrganizationAlertPolicyRevision.policy_revision
            > OrganizationAlertPolicyRevision.reconciled_revision,
        )
        .first()
        is not None
    )


def _delivery_reconciliation_is_pending(
    db: Session,
    *,
    organization_id: uuid.UUID,
) -> bool:
    checkpoint = load_pipeline_checkpoint(
        db,
        organization_id=organization_id,
        pipeline_name=(
            PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1
        ),
    )

    query = (
        db.query(
            WebhookSubscription.id
        )
        .filter(
            WebhookSubscription.organization_id
            == organization_id,
        )
    )

    if checkpoint is not None:
        query = query.filter(
            or_(
                WebhookSubscription.created_at
                > checkpoint.source_created_at,
                and_(
                    WebhookSubscription.created_at
                    == checkpoint.source_created_at,
                    WebhookSubscription.id
                    > checkpoint.source_record_id,
                ),
            )
        )

    return (
        query.first()
        is not None
    )


def discover_alert_reconciliation_source(
    db: Session,
    *,
    organization_id,
) -> PipelineSourceDiscovery | None:
    """
    Discover explicit Alert reconciliation work for one organization.

    Reconciliation is pending when either:
      - Watchlist Alert policy has an unreconciled revision; or
      - a WebhookSubscription exists after the durable delivery
        reconciliation checkpoint.

    A configuration mutation is not knowledge evidence. Organizations without
    deterministic Risk v2 evidence therefore return None even when Action Layer
    state is pending.

    The caller owns commit/rollback. This service is read-only.
    """

    organization_uuid = _as_uuid(
        organization_id,
        field_name="organization_id",
    )

    policy_pending = (
        _policy_reconciliation_is_pending(
            db,
            organization_id=organization_uuid,
        )
    )

    delivery_pending = (
        _delivery_reconciliation_is_pending(
            db,
            organization_id=organization_uuid,
        )
    )

    if not (
        policy_pending
        or delivery_pending
    ):
        return None

    return _build_alert_reconciliation_source(
        db,
        organization_id=organization_uuid,
    )


def discover_pending_alert_reconciliation_sources(
    db: Session,
    *,
    limit: int = 10,
) -> list[PipelineSourceDiscovery]:
    """
    Discover organizations with explicit pending Alert reconciliation work.

    Selection requires an existing deterministic Risk v2 knowledge source and
    at least one pending Action Layer condition:

      - policy_revision > reconciled_revision; or
      - WebhookSubscription materialization is newer than the durable delivery
        reconciliation checkpoint.

    Webhook activity state is intentionally irrelevant to source progress.
    Inactive newly-created subscriptions must still be consumed so the delivery
    reconciliation checkpoint can advance past them.

    The caller owns commit/rollback. This service is read-only.
    """

    normalized_limit = _normalize_limit(
        limit
    )

    latest_source = (
        db.query(
            RiskAssessment.organization_id.label(
                "organization_id"
            ),
            RiskAssessment.created_at.label(
                "source_created_at"
            ),
            RiskAssessment.id.label(
                "source_record_id"
            ),
        )
        .filter(
            RiskAssessment.risk_version
            == RISK_VERSION_V2
        )
        .distinct(
            RiskAssessment.organization_id
        )
        .order_by(
            RiskAssessment.organization_id.asc(),
            RiskAssessment.created_at.desc(),
            RiskAssessment.id.desc(),
        )
        .subquery()
    )

    delivery_checkpoint = (
        db.query(
            PipelineCheckpoint
        )
        .filter(
            PipelineCheckpoint.pipeline_name
            == PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1
        )
        .subquery()
    )

    delivery_pending = exists().where(
        and_(
            WebhookSubscription.organization_id
            == latest_source.c.organization_id,
            or_(
                delivery_checkpoint.c.id.is_(None),
                WebhookSubscription.created_at
                > delivery_checkpoint.c.source_created_at,
                and_(
                    WebhookSubscription.created_at
                    == delivery_checkpoint.c.source_created_at,
                    WebhookSubscription.id
                    > delivery_checkpoint.c.source_record_id,
                ),
            ),
        )
    )

    pending_rows = (
        db.query(
            latest_source.c.organization_id,
            latest_source.c.source_created_at,
            latest_source.c.source_record_id,
        )
        .outerjoin(
            OrganizationAlertPolicyRevision,
            OrganizationAlertPolicyRevision.organization_id
            == latest_source.c.organization_id,
        )
        .outerjoin(
            delivery_checkpoint,
            delivery_checkpoint.c.organization_id
            == latest_source.c.organization_id,
        )
        .outerjoin(
            OrganizationWorkSchedule,
            and_(
                OrganizationWorkSchedule.organization_id
                == latest_source.c.organization_id,
                OrganizationWorkSchedule.work_kind
                == WORK_KIND_ALERT_RECONCILIATION,
            ),
        )
        .filter(
            or_(
                OrganizationAlertPolicyRevision.policy_revision
                > OrganizationAlertPolicyRevision.reconciled_revision,
                delivery_pending,
            ),
            or_(
                OrganizationWorkSchedule.organization_id.is_(None),
                OrganizationWorkSchedule.retry_after
                <= func.now(),
            ),
        )
        .order_by(
            latest_source.c.source_created_at.asc(),
            latest_source.c.source_record_id.asc(),
            latest_source.c.organization_id.asc(),
        )
        .limit(
            normalized_limit
        )
        .all()
    )

    results: list[
        PipelineSourceDiscovery
    ] = []

    for row in pending_rows:
        discovered = (
            discover_alert_reconciliation_source(
                db,
                organization_id=(
                    row.organization_id
                ),
            )
        )

        # Re-check the pending state after candidate selection. Another worker
        # may have reconciled the organization before detailed discovery.
        if discovered is None:
            continue

        results.append(
            discovered
        )

    return results


def discover_pipeline_source(
    db: Session,
    *,
    organization_id,
    pipeline_name: str,
) -> PipelineSourceDiscovery | None:
    """
    Discover pending organization-level work for one pipeline stage.

    Returns None when:
      - the organization has no deterministic Risk v2 evidence; or
      - the pipeline checkpoint already covers the newest source
        watermark.

    A newer materialization always makes the stage pending, including
    Risk v2 backfills for old events.

    temporal_cursor is monotonic:
      max(
          maximum assessed_at visible through source watermark,
          previous checkpoint knowledge_cursor,
      )

    The caller owns commit/rollback. This service is read-only.
    """

    organization_uuid = _as_uuid(
        organization_id,
        field_name="organization_id",
    )

    checkpoint = load_pipeline_checkpoint(
        db,
        organization_id=organization_uuid,
        pipeline_name=pipeline_name,
    )

    latest = _latest_source_row(
        db,
        organization_id=organization_uuid,
    )

    if latest is None:
        return None

    source_record_id = latest[0]
    source_created_at = latest[1]

    if not source_watermark_is_newer(
        candidate_created_at=source_created_at,
        candidate_record_id=source_record_id,
        checkpoint=checkpoint,
    ):
        return None

    max_assessed_at = (
        _max_visible_assessed_at(
            db,
            organization_id=organization_uuid,
            source_created_at=source_created_at,
            source_record_id=source_record_id,
        )
    )

    if max_assessed_at is None:
        raise RuntimeError(
            "A Risk v2 source watermark exists but no "
            "visible assessed_at value could be resolved."
        )

    temporal_cursor = max_assessed_at

    if (
        checkpoint is not None
        and checkpoint.knowledge_cursor
        > temporal_cursor
    ):
        temporal_cursor = (
            checkpoint.knowledge_cursor
        )

    return PipelineSourceDiscovery(
        organization_id=organization_uuid,
        pipeline_name=pipeline_name,
        source_created_at=source_created_at,
        source_record_id=source_record_id,
        temporal_cursor=temporal_cursor,
    )

def _normalize_pipeline_name(
    value: str,
) -> str:
    if not isinstance(value, str):
        raise PipelineSourceDiscoveryValidationError(
            "pipeline_name must be a string."
        )

    normalized = value.strip()

    if not normalized:
        raise PipelineSourceDiscoveryValidationError(
            "pipeline_name must not be empty."
        )

    if len(normalized) > 64:
        raise PipelineSourceDiscoveryValidationError(
            "pipeline_name must not exceed 64 characters."
        )

    return normalized


def _normalize_limit(
    value: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
    ):
        raise PipelineSourceDiscoveryValidationError(
            "limit must be an integer of at least 1."
        )

    return value


def discover_pending_pipeline_sources(
    db: Session,
    *,
    pipeline_name: str,
    limit: int = 10,
) -> list[PipelineSourceDiscovery]:
    """
    Discover organizations whose deterministic Risk v2 source
    watermark is ahead of the supplied pipeline checkpoint.

    Selection guarantees:
      - deterministic Risk v2 only;
      - at most one row per organization;
      - missing checkpoints are pending;
      - stale checkpoints are pending;
      - up-to-date checkpoints are excluded;
      - source ordering is deterministic;
      - result count is bounded by limit.

    Ordering is oldest pending materialization first so a busy
    organization cannot indefinitely starve older organization work.

    The caller owns commit/rollback. This service is read-only.
    """

    normalized_pipeline_name = (
        _normalize_pipeline_name(
            pipeline_name
        )
    )
    normalized_limit = _normalize_limit(
        limit
    )

    # PostgreSQL DISTINCT ON selects exactly one newest Risk v2
    # materialization row per organization.
    latest_source = (
        db.query(
            RiskAssessment.organization_id.label(
                "organization_id"
            ),
            RiskAssessment.created_at.label(
                "source_created_at"
            ),
            RiskAssessment.id.label(
                "source_record_id"
            ),
        )
        .filter(
            RiskAssessment.risk_version
            == RISK_VERSION_V2
        )
        .distinct(
            RiskAssessment.organization_id
        )
        .order_by(
            RiskAssessment.organization_id.asc(),
            RiskAssessment.created_at.desc(),
            RiskAssessment.id.desc(),
        )
        .subquery()
    )

    pending_rows = (
        db.query(
            latest_source.c.organization_id,
            latest_source.c.source_created_at,
            latest_source.c.source_record_id,
        )
        .outerjoin(
            PipelineCheckpoint,
            and_(
                PipelineCheckpoint.organization_id
                == latest_source.c.organization_id,
                PipelineCheckpoint.pipeline_name
                == normalized_pipeline_name,
            ),
        )
        .outerjoin(
            OrganizationWorkSchedule,
            and_(
                OrganizationWorkSchedule.organization_id
                == latest_source.c.organization_id,
                OrganizationWorkSchedule.work_kind
                == WORK_KIND_INTELLIGENCE,
            ),
        )
        .filter(
            or_(
                PipelineCheckpoint.id.is_(None),
                latest_source.c.source_created_at
                > PipelineCheckpoint.source_created_at,
                and_(
                    latest_source.c.source_created_at
                    == PipelineCheckpoint.source_created_at,
                    latest_source.c.source_record_id
                    > PipelineCheckpoint.source_record_id,
                ),
            ),
            or_(
                OrganizationWorkSchedule.organization_id.is_(None),
                OrganizationWorkSchedule.retry_after
                <= func.now(),
            ),
        )
        .order_by(
            latest_source.c.source_created_at.asc(),
            latest_source.c.source_record_id.asc(),
            latest_source.c.organization_id.asc(),
        )
        .limit(
            normalized_limit
        )
        .all()
    )

    results: list[
        PipelineSourceDiscovery
    ] = []

    for row in pending_rows:
        discovered = discover_pipeline_source(
            db,
            organization_id=(
                row.organization_id
            ),
            pipeline_name=(
                normalized_pipeline_name
            ),
        )

        # Another worker may have advanced the checkpoint between
        # candidate selection and detailed discovery. In that case
        # the organization is no longer pending.
        if discovered is None:
            continue

        results.append(
            discovered
        )

    return results