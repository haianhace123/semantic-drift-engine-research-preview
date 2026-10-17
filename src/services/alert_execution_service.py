"""
Production execution path for deterministic Alert generation.

Flow:
    deterministic Risk v2 source range
    -> Watchlist policy
    -> AlertCandidate generation
    -> deterministic Alert persistence
    -> durable source materialization checkpoints
    -> durable Alert checkpoint

Pattern and Anomaly rows expose independent materialization positions through
(created_at, id). Alert generation records those positions transactionally
after persistence and delivery fanout succeed.

Historical effective-time Pattern and Anomaly scans are temporarily retained
for policy and delivery reconciliation. They will be removed from the normal
hot path only after those reconciliation responsibilities have explicit
revision-triggered execution paths.

Important orchestration invariant:
- Pattern and Anomaly detection for a Risk v2 source watermark must complete
before this Alert stage advances any corresponding Alert checkpoint.

Transaction ownership remains with the caller:
- no commit;
- no rollback;
- no external delivery;
- no Slack/Webhook calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from src.models.alert import Alert
from src.models.anomaly import Anomaly
from src.models.monitored_page import MonitoredPage
from src.models.pattern import Pattern
from src.models.pipeline_checkpoint import (
    PIPELINE_ALERT_ANOMALY_MATERIALIZATION_V1,
    PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1,
    PIPELINE_ALERT_GENERATION_V1,
    PIPELINE_ALERT_PATTERN_MATERIALIZATION_V1,
)
from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import SemanticDriftEvent
from src.models.watchlist import Watchlist
from src.models.webhook_subscription import WebhookSubscription
from src.services.alert_candidate_service import (
    build_anomaly_alert_candidate,
    build_pattern_alert_candidate,
    build_risk_alert_candidate,
)
from src.services.alert_service import (
    persist_alert_candidate,
)
from src.services.alert_delivery_fanout_service import (
    fanout_alert_deliveries,
    reconcile_alert_deliveries,
)
from src.services.alert_policy_revision_service import (
    load_alert_policy_revision,
    mark_alert_policy_reconciled,
)
from src.services.organization_execution_lock_service import (
    acquire_organization_execution_lock,
)
from src.services.pipeline_checkpoint_service import (
    PipelineCheckpointResult,
    load_pipeline_checkpoint,
    persist_pipeline_checkpoint,
)
from src.services.persisted_pattern_qualification_service import (
    qualified_pattern_ids,
)
from src.services.pipeline_source_discovery_service import (
    PipelineSourceDiscovery,
)
from src.services.risk_assessment_service import (
    RISK_VERSION_V2,
)


ALERT_PIPELINE_NAME = PIPELINE_ALERT_GENERATION_V1
ALERT_DELIVERY_RECONCILIATION_BATCH_SIZE = 500
ALERT_WEBHOOK_RECONCILIATION_BATCH_SIZE = 500
ALERT_POLICY_RECONCILIATION_BATCH_SIZE = 500


class AlertExecutionError(
    RuntimeError
):
    """Raised when deterministic Alert execution evidence is inconsistent."""


@dataclass(frozen=True)
class AlertExecutionResult:
    organization_id: uuid.UUID
    source_created_at: datetime
    source_record_id: uuid.UUID
    temporal_cursor: datetime

    risk_source_count: int
    risk_candidate_count: int

    pattern_source_count: int
    pattern_candidate_count: int

    anomaly_source_count: int
    anomaly_candidate_count: int

    alert_created_count: int
    alert_existing_count: int

    checkpoint: PipelineCheckpointResult


def _risk_lower_boundary(
    checkpoint,
):
    if checkpoint is None:
        return None

    return or_(
        RiskAssessment.created_at
        > checkpoint.source_created_at,
        and_(
            RiskAssessment.created_at
            == checkpoint.source_created_at,
            RiskAssessment.id
            > checkpoint.source_record_id,
        ),
    )


def _risk_upper_boundary(
    source: PipelineSourceDiscovery,
):
    return or_(
        RiskAssessment.created_at
        < source.source_created_at,
        and_(
            RiskAssessment.created_at
            == source.source_created_at,
            RiskAssessment.id
            <= source.source_record_id,
        ),
    )


def _load_risk_sources(
    db: Session,
    *,
    source: PipelineSourceDiscovery,
    checkpoint,
) -> tuple[RiskAssessment, ...]:
    """
    Load the complete deterministic Risk v2 materialization range:

        checkpoint < (created_at, id) <= discovered source watermark
    """
    query = (
        db.query(
            RiskAssessment
        )
        .filter(
            RiskAssessment.organization_id
            == source.organization_id,
            RiskAssessment.risk_version
            == RISK_VERSION_V2,
            _risk_upper_boundary(
                source
            ),
        )
    )

    lower_boundary = (
        _risk_lower_boundary(
            checkpoint
        )
    )

    if lower_boundary is not None:
        query = query.filter(
            lower_boundary
        )

    return tuple(
        query
        .order_by(
            RiskAssessment.created_at.asc(),
            RiskAssessment.id.asc(),
        )
        .all()
    )


def _load_evidence_maps(
    db: Session,
    *,
    organization_id: uuid.UUID,
    risk_sources: tuple[RiskAssessment, ...],
    anomalies: tuple[Anomaly, ...],
) -> tuple[
    dict[uuid.UUID, SemanticDriftEvent],
    dict[uuid.UUID, MonitoredPage],
]:
    """
    Bulk-load tenant-visible evidence shared by Risk and Anomaly candidates.

    Missing or cross-tenant evidence is intentionally absent from the returned
    maps. Candidate loops retain source-specific fail-closed validation and
    error messages without issuing per-source database queries.
    """
    event_ids = {
        event_id
        for event_id in (
            assessment.semantic_drift_event_id
            for assessment in risk_sources
        )
        if event_id is not None
    }

    event_ids.update(
        anomaly.event_id
        for anomaly in anomalies
    )

    if not event_ids:
        return (
            {},
            {},
        )

    events = (
        db.query(
            SemanticDriftEvent
        )
        .filter(
            SemanticDriftEvent.organization_id
            == organization_id,
            SemanticDriftEvent.id.in_(
                event_ids
            ),
        )
        .all()
    )

    event_by_id = {
        event.id: event
        for event in events
    }

    page_ids = {
        event.monitored_page_id
        for event in events
    }

    if not page_ids:
        return (
            event_by_id,
            {},
        )

    monitored_pages = (
        db.query(
            MonitoredPage
        )
        .filter(
            MonitoredPage.organization_id
            == organization_id,
            MonitoredPage.id.in_(
                page_ids
            ),
        )
        .all()
    )

    page_by_id = {
        page.id: page
        for page in monitored_pages
    }

    return (
        event_by_id,
        page_by_id,
    )


def _load_watchlists(
    db: Session,
    *,
    organization_id: uuid.UUID,
) -> tuple[Watchlist, ...]:
    """
    Load organization Watchlists once for deterministic policy evaluation.

    Candidate policy itself decides whether inactive or archived Watchlists
    participate.
    """
    return tuple(
        db.query(
            Watchlist
        )
        .filter(
            Watchlist.organization_id
            == organization_id,
        )
        .order_by(
            Watchlist.id.asc()
        )
        .all()
    )


def _materialization_lower_boundary(
    model,
    checkpoint,
):
    if checkpoint is None:
        return None

    return or_(
        model.created_at
        > checkpoint.source_created_at,
        and_(
            model.created_at
            == checkpoint.source_created_at,
            model.id
            > checkpoint.source_record_id,
        ),
    )


def _load_visible_pattern_reconciliation_batch(
    db: Session,
    *,
    organization_id: uuid.UUID,
    temporal_cursor: datetime,
    after_created_at: datetime | None,
    after_id: uuid.UUID | None,
    limit: int,
) -> tuple[Pattern, ...]:
    """
    Load one bounded Pattern policy-reconciliation batch.

    created_at is the durable materialization cursor. detected_at remains the
    effective-time visibility bound and must not participate in pagination.
    """
    if limit <= 0:
        raise AlertExecutionError(
            "Pattern policy reconciliation batch limit must be positive."
        )

    query = (
        db.query(
            Pattern
        )
        .filter(
            Pattern.organization_id
            == organization_id,
            Pattern.detected_at
            <= temporal_cursor,
        )
    )

    if after_created_at is not None:
        if after_id is None:
            raise AlertExecutionError(
                "Pattern policy reconciliation cursor requires after_id."
            )

        query = query.filter(
            or_(
                Pattern.created_at
                > after_created_at,
                and_(
                    Pattern.created_at
                    == after_created_at,
                    Pattern.id
                    > after_id,
                ),
            )
        )

    elif after_id is not None:
        raise AlertExecutionError(
            "Pattern policy reconciliation cursor requires after_created_at."
        )

    return tuple(
        query
        .order_by(
            Pattern.created_at.asc(),
            Pattern.id.asc(),
        )
        .limit(limit)
        .all()
    )

def _load_visible_anomaly_reconciliation_batch(
    db: Session,
    *,
    organization_id: uuid.UUID,
    temporal_cursor: datetime,
    after_created_at: datetime | None,
    after_id: uuid.UUID | None,
    limit: int,
) -> tuple[Anomaly, ...]:
    """
    Load one bounded Anomaly policy-reconciliation batch.

    created_at is the durable materialization cursor. detected_at remains the
    effective-time visibility bound and must not participate in pagination.
    """
    if limit <= 0:
        raise AlertExecutionError(
            "Anomaly policy reconciliation batch limit must be positive."
        )

    query = (
        db.query(
            Anomaly
        )
        .filter(
            Anomaly.organization_id
            == organization_id,
            Anomaly.detected_at
            <= temporal_cursor,
        )
    )

    if after_created_at is not None:
        if after_id is None:
            raise AlertExecutionError(
                "Anomaly policy reconciliation cursor requires after_id."
            )

        query = query.filter(
            or_(
                Anomaly.created_at
                > after_created_at,
                and_(
                    Anomaly.created_at
                    == after_created_at,
                    Anomaly.id
                    > after_id,
                ),
            )
        )

    elif after_id is not None:
        raise AlertExecutionError(
            "Anomaly policy reconciliation cursor requires after_created_at."
        )

    return tuple(
        query
        .order_by(
            Anomaly.created_at.asc(),
            Anomaly.id.asc(),
        )
        .limit(limit)
        .all()
    )

def _load_materialized_patterns(
    db: Session,
    *,
    organization_id: uuid.UUID,
    checkpoint,
) -> tuple[Pattern, ...]:
    """
    Load Patterns materialized strictly after the supplied knowledge-time
    checkpoint.

    Pattern.created_at is the durable materialization position. detected_at
    remains effective time and must not participate in this boundary.
    """
    query = (
        db.query(
            Pattern
        )
        .filter(
            Pattern.organization_id
            == organization_id,
        )
    )

    lower_boundary = (
        _materialization_lower_boundary(
            Pattern,
            checkpoint,
        )
    )

    if lower_boundary is not None:
        query = query.filter(
            lower_boundary
        )

    return tuple(
        query
        .order_by(
            Pattern.created_at.asc(),
            Pattern.id.asc(),
        )
        .all()
    )


def _load_materialized_anomalies(
    db: Session,
    *,
    organization_id: uuid.UUID,
    checkpoint,
) -> tuple[Anomaly, ...]:
    """
    Load Anomalies materialized strictly after the supplied knowledge-time
    checkpoint.

    Anomaly.created_at is the durable materialization position. detected_at
    remains effective time and must not participate in this boundary.
    """
    query = (
        db.query(
            Anomaly
        )
        .filter(
            Anomaly.organization_id
            == organization_id,
        )
    )

    lower_boundary = (
        _materialization_lower_boundary(
            Anomaly,
            checkpoint,
        )
    )

    if lower_boundary is not None:
        query = query.filter(
            lower_boundary
        )

    return tuple(
        query
        .order_by(
            Anomaly.created_at.asc(),
            Anomaly.id.asc(),
        )
        .all()
    )

def _load_webhook_reconciliation_batch(
    db: Session,
    *,
    organization_id: uuid.UUID,
    after_created_at: datetime | None,
    after_id: uuid.UUID | None,
    limit: int,
) -> tuple[WebhookSubscription, ...]:
    if limit <= 0:
        raise AlertExecutionError(
            "Webhook reconciliation batch limit must be positive."
        )

    query = (
        db.query(
            WebhookSubscription
        )
        .filter(
            WebhookSubscription.organization_id
            == organization_id
        )
    )

    if after_created_at is not None:
        if after_id is None:
            raise AlertExecutionError(
                "Webhook reconciliation cursor requires after_id."
            )

        query = query.filter(
            or_(
                WebhookSubscription.created_at
                > after_created_at,
                and_(
                    WebhookSubscription.created_at
                    == after_created_at,
                    WebhookSubscription.id
                    > after_id,
                ),
            )
        )
    elif after_id is not None:
        raise AlertExecutionError(
            "Webhook reconciliation cursor requires after_created_at."
        )

    return tuple(
        query
        .order_by(
            WebhookSubscription.created_at.asc(),
            WebhookSubscription.id.asc(),
        )
        .limit(limit)
        .all()
    )


def _load_alert_delivery_reconciliation_batch(
    db: Session,
    *,
    organization_id: uuid.UUID,
    after_created_at: datetime | None,
    after_id: uuid.UUID | None,
    limit: int,
) -> tuple[Alert, ...]:
    if limit <= 0:
        raise AlertExecutionError(
            "Alert delivery reconciliation batch limit must be positive."
        )

    query = (
        db.query(Alert)
        .filter(
            Alert.organization_id == organization_id,
        )
    )

    if after_created_at is not None:
        if after_id is None:
            raise AlertExecutionError(
                "Alert delivery reconciliation cursor requires after_id."
            )

        query = query.filter(
            or_(
                Alert.created_at > after_created_at,
                and_(
                    Alert.created_at == after_created_at,
                    Alert.id > after_id,
                ),
            )
        )
    elif after_id is not None:
        raise AlertExecutionError(
            "Alert delivery reconciliation cursor requires after_created_at."
        )

    return tuple(
        query
        .order_by(
            Alert.created_at.asc(),
            Alert.id.asc(),
        )
        .limit(limit)
        .all()
    )

def _validate_source(
    source: PipelineSourceDiscovery,
) -> None:
    if (
        source.pipeline_name
        != ALERT_PIPELINE_NAME
    ):
        raise AlertExecutionError(
            "PipelineSourceDiscovery pipeline_name does not "
            f"match {ALERT_PIPELINE_NAME!r}."
        )

def execute_alert_generation(
    db: Session,
    *,
    source: PipelineSourceDiscovery,
) -> AlertExecutionResult:
    """
    Generate and persist deterministic Alerts for one pending organization.

    Risk handling:
        consumes every Risk v2 materialization strictly after the current
        Alert checkpoint and through the supplied source watermark.

    Pattern handling:
        consumes newly materialized Patterns by knowledge-time checkpoint.
        Historical visible Patterns are re-scanned only when Watchlist policy
        reconciliation is pending.

    Anomaly handling:
        consumes newly materialized Anomalies by knowledge-time checkpoint.
        Historical visible Anomalies are re-scanned only when Watchlist policy
        reconciliation is pending.

    Checkpoint handling:
        advances only after all Alert candidates have been evaluated and
        persisted. Alert rows and checkpoint advancement therefore remain in
        the caller's single transaction.

    The caller owns commit/rollback.
    """
    _validate_source(
        source
    )

    acquire_organization_execution_lock(
        db,
        organization_id=(
            source.organization_id
        ),
    )

    policy_revision = (
        load_alert_policy_revision(
            db,
            organization_id=(
                source.organization_id
            ),
            for_update=True,
        )
    )

    policy_reconciliation_required = (
        policy_revision is not None
        and policy_revision.reconciliation_required
    )

    checkpoint = (
        load_pipeline_checkpoint(
            db,
            organization_id=(
                source.organization_id
            ),
            pipeline_name=(
                ALERT_PIPELINE_NAME
            ),
        )
    )

    pattern_materialization_checkpoint = (
        load_pipeline_checkpoint(
            db,
            organization_id=(
                source.organization_id
            ),
            pipeline_name=(
                PIPELINE_ALERT_PATTERN_MATERIALIZATION_V1
            ),
        )
    )

    anomaly_materialization_checkpoint = (
        load_pipeline_checkpoint(
            db,
            organization_id=(
                source.organization_id
            ),
            pipeline_name=(
                PIPELINE_ALERT_ANOMALY_MATERIALIZATION_V1
            ),
        )
    )

    delivery_reconciliation_checkpoint = (
        load_pipeline_checkpoint(
            db,
            organization_id=(
                source.organization_id
            ),
            pipeline_name=(
                PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1
            ),
        )
    )

    risk_sources = (
        _load_risk_sources(
            db,
            source=source,
            checkpoint=checkpoint,
        )
    )

    materialized_patterns = (
        _load_materialized_patterns(
            db,
            organization_id=(
                source.organization_id
            ),
            checkpoint=(
                pattern_materialization_checkpoint
            ),
        )
    )

    materialized_anomalies = (
        _load_materialized_anomalies(
            db,
            organization_id=(
                source.organization_id
            ),
            checkpoint=(
                anomaly_materialization_checkpoint
            ),
        )
    )

    watchlists = (
        _load_watchlists(
            db,
            organization_id=(
                source.organization_id
            ),
        )
    )

    anomalies = materialized_anomalies

    materialized_anomaly_ids = {
        anomaly.id
        for anomaly in materialized_anomalies
    }

    (
        event_by_id,
        page_by_id,
    ) = _load_evidence_maps(
        db,
        organization_id=(
            source.organization_id
        ),
        risk_sources=risk_sources,
        anomalies=anomalies,
    )

    alert_created_count = 0
    alert_existing_count = 0

    risk_candidate_count = 0

    for assessment in risk_sources:
        event_id = (
            assessment.semantic_drift_event_id
        )

        if event_id is None:
            event = None
            monitored_page = None

        else:
            event = event_by_id.get(
                event_id
            )

            if event is None:
                raise AlertExecutionError(
                    "RiskAssessment references a SemanticDriftEvent "
                    "that is not visible in the organization."
                )

            monitored_page = page_by_id.get(
                event.monitored_page_id
            )

            if monitored_page is None:
                raise AlertExecutionError(
                    "SemanticDriftEvent references a MonitoredPage "
                    "that is not visible in the organization."
                )

        candidate = (
            build_risk_alert_candidate(
                organization_id=(
                    source.organization_id
                ),
                assessment=assessment,
                event=event,
                monitored_page=(
                    monitored_page
                ),
                watchlists=watchlists,
            )
        )

        if candidate is None:
            continue

        risk_candidate_count += 1

        persisted_alert = persist_alert_candidate(
            db,
            candidate=candidate,
        )

        if persisted_alert.created:
            alert_created_count += 1
        else:
            alert_existing_count += 1

        fanout_alert_deliveries(
            db,
            organization_id=source.organization_id,
            alert_id=persisted_alert.alert_id,
        )

    patterns = materialized_patterns
    qualified_materialized_pattern_ids = qualified_pattern_ids(
        db,
        organization_id=source.organization_id,
        patterns=patterns,
    )

    materialized_pattern_ids = {
        pattern.id
        for pattern in materialized_patterns
    }

    pattern_source_count = len(
        materialized_patterns
    )
    pattern_candidate_count = 0

    for pattern in patterns:
        if pattern.id not in qualified_materialized_pattern_ids:
            continue
        candidate = (
            build_pattern_alert_candidate(
                organization_id=(
                    source.organization_id
                ),
                pattern=pattern,
                watchlists=watchlists,
            )
        )

        if candidate is None:
            continue

        pattern_candidate_count += 1

        persisted_alert = persist_alert_candidate(
            db,
            candidate=candidate,
        )

        if persisted_alert.created:
            alert_created_count += 1
        else:
            alert_existing_count += 1

        fanout_alert_deliveries(
            db,
            organization_id=source.organization_id,
            alert_id=persisted_alert.alert_id,
        )

    if policy_reconciliation_required:
        after_pattern_created_at = None
        after_pattern_id = None

        while True:
            pattern_batch = (
                _load_visible_pattern_reconciliation_batch(
                    db,
                    organization_id=(
                        source.organization_id
                    ),
                    temporal_cursor=(
                        source.temporal_cursor
                    ),
                    after_created_at=(
                        after_pattern_created_at
                    ),
                    after_id=after_pattern_id,
                    limit=(
                        ALERT_POLICY_RECONCILIATION_BATCH_SIZE
                    ),
                )
            )

            if not pattern_batch:
                break

            qualified_batch_pattern_ids = qualified_pattern_ids(
                db,
                organization_id=source.organization_id,
                patterns=pattern_batch,
            )

            for pattern in pattern_batch:
                if pattern.id not in qualified_batch_pattern_ids:
                    continue
                if (
                    pattern.id
                    in materialized_pattern_ids
                ):
                    continue

                pattern_source_count += 1

                candidate = (
                    build_pattern_alert_candidate(
                        organization_id=(
                            source.organization_id
                        ),
                        pattern=pattern,
                        watchlists=watchlists,
                    )
                )

                if candidate is None:
                    continue

                pattern_candidate_count += 1

                persisted_alert = persist_alert_candidate(
                    db,
                    candidate=candidate,
                )

                if persisted_alert.created:
                    alert_created_count += 1
                else:
                    alert_existing_count += 1

                fanout_alert_deliveries(
                    db,
                    organization_id=source.organization_id,
                    alert_id=persisted_alert.alert_id,
                )

            latest_pattern = pattern_batch[-1]
            after_pattern_created_at = (
                latest_pattern.created_at
            )
            after_pattern_id = latest_pattern.id

    anomaly_source_count = len(
        materialized_anomalies
    )
    anomaly_candidate_count = 0

    for anomaly in materialized_anomalies:
        event = event_by_id.get(
            anomaly.event_id
        )

        if event is None:
            raise AlertExecutionError(
                "Anomaly references a SemanticDriftEvent "
                "that is not visible in the organization."
            )

        monitored_page = page_by_id.get(
            event.monitored_page_id
        )

        if monitored_page is None:
            raise AlertExecutionError(
                "Anomaly SemanticDriftEvent references a MonitoredPage "
                "that is not visible in the organization."
            )

        candidate = (
            build_anomaly_alert_candidate(
                organization_id=(
                    source.organization_id
                ),
                anomaly=anomaly,
                event=event,
                monitored_page=(
                    monitored_page
                ),
                watchlists=watchlists,
            )
        )

        if candidate is None:
            continue

        anomaly_candidate_count += 1

        persisted_alert = persist_alert_candidate(
            db,
            candidate=candidate,
        )

        if persisted_alert.created:
            alert_created_count += 1
        else:
            alert_existing_count += 1

        fanout_alert_deliveries(
            db,
            organization_id=source.organization_id,
            alert_id=persisted_alert.alert_id,
        )

    if policy_reconciliation_required:
        after_anomaly_created_at = None
        after_anomaly_id = None

        while True:
            anomaly_batch = (
                _load_visible_anomaly_reconciliation_batch(
                    db,
                    organization_id=(
                        source.organization_id
                    ),
                    temporal_cursor=(
                        source.temporal_cursor
                    ),
                    after_created_at=(
                        after_anomaly_created_at
                    ),
                    after_id=after_anomaly_id,
                    limit=(
                        ALERT_POLICY_RECONCILIATION_BATCH_SIZE
                    ),
                )
            )

            if not anomaly_batch:
                break

            historical_anomalies = tuple(
                anomaly
                for anomaly in anomaly_batch
                if (
                    anomaly.id
                    not in materialized_anomaly_ids
                )
            )

            anomaly_source_count += len(
                historical_anomalies
            )

            if historical_anomalies:
                (
                    historical_event_by_id,
                    historical_page_by_id,
                ) = _load_evidence_maps(
                    db,
                    organization_id=(
                        source.organization_id
                    ),
                    risk_sources=(),
                    anomalies=historical_anomalies,
                )

                for anomaly in historical_anomalies:
                    event = (
                        historical_event_by_id.get(
                            anomaly.event_id
                        )
                    )

                    if event is None:
                        raise AlertExecutionError(
                            "Anomaly references a SemanticDriftEvent "
                            "that is not visible in the organization."
                        )

                    monitored_page = (
                        historical_page_by_id.get(
                            event.monitored_page_id
                        )
                    )

                    if monitored_page is None:
                        raise AlertExecutionError(
                            "Anomaly SemanticDriftEvent references a "
                            "MonitoredPage that is not visible in the "
                            "organization."
                        )

                    candidate = (
                        build_anomaly_alert_candidate(
                            organization_id=(
                                source.organization_id
                            ),
                            anomaly=anomaly,
                            event=event,
                            monitored_page=(
                                monitored_page
                            ),
                            watchlists=watchlists,
                        )
                    )

                    if candidate is None:
                        continue

                    anomaly_candidate_count += 1

                    persisted_alert = persist_alert_candidate(
                        db,
                        candidate=candidate,
                    )

                    if persisted_alert.created:
                        alert_created_count += 1
                    else:
                        alert_existing_count += 1

                    fanout_alert_deliveries(
                        db,
                        organization_id=source.organization_id,
                        alert_id=persisted_alert.alert_id,
                    )

            latest_anomaly = anomaly_batch[-1]
            after_anomaly_created_at = (
                latest_anomaly.created_at
            )
            after_anomaly_id = latest_anomaly.id

    webhook_source_count = 0
    latest_webhook_subscription = None

    after_webhook_created_at = (
        delivery_reconciliation_checkpoint.source_created_at
        if delivery_reconciliation_checkpoint is not None
        else None
    )
    after_webhook_id = (
        delivery_reconciliation_checkpoint.source_record_id
        if delivery_reconciliation_checkpoint is not None
        else None
    )

    while True:
        webhook_batch = (
            _load_webhook_reconciliation_batch(
                db,
                organization_id=(
                    source.organization_id
                ),
                after_created_at=(
                    after_webhook_created_at
                ),
                after_id=after_webhook_id,
                limit=(
                    ALERT_WEBHOOK_RECONCILIATION_BATCH_SIZE
                ),
            )
        )

        if not webhook_batch:
            break

        webhook_source_count += len(
            webhook_batch
        )

        after_alert_created_at = None
        after_alert_id = None

        while True:
            alert_batch = (
                _load_alert_delivery_reconciliation_batch(
                    db,
                    organization_id=(
                        source.organization_id
                    ),
                    after_created_at=(
                        after_alert_created_at
                    ),
                    after_id=after_alert_id,
                    limit=(
                        ALERT_DELIVERY_RECONCILIATION_BATCH_SIZE
                    ),
                )
            )

            if not alert_batch:
                break

            reconcile_alert_deliveries(
                db,
                organization_id=(
                    source.organization_id
                ),
                alerts=alert_batch,
                subscriptions=webhook_batch,
            )

            latest_alert = alert_batch[-1]
            after_alert_created_at = (
                latest_alert.created_at
            )
            after_alert_id = latest_alert.id

        latest_webhook_subscription = (
            webhook_batch[-1]
        )
        after_webhook_created_at = (
            latest_webhook_subscription.created_at
        )
        after_webhook_id = (
            latest_webhook_subscription.id
        )

    if policy_reconciliation_required:
        mark_alert_policy_reconciled(
            db,
            organization_id=(
                source.organization_id
            ),
            policy_revision=(
                policy_revision.policy_revision
            ),
        )

    if latest_webhook_subscription is not None:
        persist_pipeline_checkpoint(
            db,
            organization_id=(
                source.organization_id
            ),
            pipeline_name=(
                PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1
            ),
            source_created_at=(
                latest_webhook_subscription.created_at
            ),
            source_record_id=(
                latest_webhook_subscription.id
            ),
            knowledge_cursor=(
                source.temporal_cursor
            ),
            metadata_json={
                "source_count": (
                    webhook_source_count
                ),
            },
        )


    if materialized_patterns:
        latest_pattern = materialized_patterns[-1]

        persist_pipeline_checkpoint(
            db,
            organization_id=(
                source.organization_id
            ),
            pipeline_name=(
                PIPELINE_ALERT_PATTERN_MATERIALIZATION_V1
            ),
            source_created_at=(
                latest_pattern.created_at
            ),
            source_record_id=(
                latest_pattern.id
            ),
            knowledge_cursor=(
                source.temporal_cursor
            ),
            metadata_json={
                "source_count": len(
                    materialized_patterns
                ),
            },
        )

    if materialized_anomalies:
        latest_anomaly = materialized_anomalies[-1]

        persist_pipeline_checkpoint(
            db,
            organization_id=(
                source.organization_id
            ),
            pipeline_name=(
                PIPELINE_ALERT_ANOMALY_MATERIALIZATION_V1
            ),
            source_created_at=(
                latest_anomaly.created_at
            ),
            source_record_id=(
                latest_anomaly.id
            ),
            knowledge_cursor=(
                source.temporal_cursor
            ),
            metadata_json={
                "source_count": len(
                    materialized_anomalies
                ),
            },
        )

    checkpoint_result = (
        persist_pipeline_checkpoint(
            db,
            organization_id=(
                source.organization_id
            ),
            pipeline_name=(
                ALERT_PIPELINE_NAME
            ),
            source_created_at=(
                source.source_created_at
            ),
            source_record_id=(
                source.source_record_id
            ),
            knowledge_cursor=(
                source.temporal_cursor
            ),
            metadata_json={
                "risk_source_count": (
                    len(
                        risk_sources
                    )
                ),
                "risk_candidate_count": (
                    risk_candidate_count
                ),
                "pattern_source_count": (
                    pattern_source_count
                ),
                "pattern_candidate_count": (
                    pattern_candidate_count
                ),
                "anomaly_source_count": (
                    anomaly_source_count
                ),
                "anomaly_candidate_count": (
                    anomaly_candidate_count
                ),
                "alert_created_count": (
                    alert_created_count
                ),
                "alert_existing_count": (
                    alert_existing_count
                ),
            },
        )
    )

    return AlertExecutionResult(
        organization_id=(
            source.organization_id
        ),
        source_created_at=(
            source.source_created_at
        ),
        source_record_id=(
            source.source_record_id
        ),
        temporal_cursor=(
            source.temporal_cursor
        ),
        risk_source_count=(
            len(
                risk_sources
            )
        ),
        risk_candidate_count=(
            risk_candidate_count
        ),
        pattern_source_count=(
            pattern_source_count
        ),
        pattern_candidate_count=(
            pattern_candidate_count
        ),
        anomaly_source_count=(
            anomaly_source_count
        ),
        anomaly_candidate_count=(
            anomaly_candidate_count
        ),
        alert_created_count=(
            alert_created_count
        ),
        alert_existing_count=(
            alert_existing_count
        ),
        checkpoint=(
            checkpoint_result
        ),
    )

def test_anomaly_source_count_includes_suppressed_operator_state():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        _, page = _make_entity_and_page(
            db,
            organization_id=organization.id,
            suffix="AnomalyOperatorState",
        )

        base = datetime(
            2026,
            9,
            12,
            5,
            0,
            tzinfo=UTC,
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            page=page,
            detected_at=base,
            suffix="anomaly-operator-state",
        )

        anomaly = _make_anomaly(
            db,
            organization_id=organization.id,
            event=event,
            detected_at=(
                base
                + timedelta(minutes=5)
            ),
        )

        anomaly.is_dismissed = True

        source_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=(
                base
                + timedelta(hours=1)
            ),
            created_at=(
                base
                + timedelta(hours=2)
            ),
            score=0.20,
        )

        db.commit()

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert (
            source.source_record_id
            == source_risk.id
        )

        result = execute_alert_generation(
            db,
            source=source,
        )

        db.commit()

        assert result.anomaly_source_count == 1
        assert result.anomaly_candidate_count == 0
        assert result.alert_created_count == 0

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()
