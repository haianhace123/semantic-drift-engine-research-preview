"""
Vector semantic processing worker.

Processes three durable queues stored in PostgreSQL:

1. PageSnapshot embeddings
2. SemanticDriftEvent vector analysis
3. SemanticDriftEvent risk scoring

The worker intentionally keeps orchestration separate from the
domain services. EmbeddingService, VectorAnalysisService and
RiskScoringService own their individual lifecycle transitions
and persistence.

Examples:

    python -m scripts.vector_worker --once

    python -m scripts.vector_worker --once --snapshot-limit 5

    python -m scripts.vector_worker --once --skip-embeddings

    python -m scripts.vector_worker --once --skip-analysis

    python -m scripts.vector_worker --once --skip-risk-scoring
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, or_
from sqlalchemy.orm import Session, aliased

from src.core.database import SessionLocal
from src.models.page_snapshot import PageSnapshot
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
    TEMPORAL_INVALIDATION_STATUS_COMPLETED,
    TEMPORAL_INVALIDATION_STATUS_FAILED,
    TEMPORAL_INVALIDATION_STATUS_PENDING,
    VECTOR_ANALYSIS_STATUS_COMPLETED,
)
from src.services.embedding_service import (
    EmbeddingProviderUnavailableError,
    EmbeddingService,
)
from src.services.vector_analysis_service import (
    VectorAnalysisNotReadyError,
    VectorAnalysisService,
)
from src.models.risk_assessment import RiskAssessment
from src.services.risk_version import (
    RISK_VERSION_V2,
    RISK_VERSION_V3,
)
from src.services.risk_scoring_v3_shadow_orchestrator import (
    RiskScoringV3ShadowNotReadyError,
    RiskScoringV3ShadowService,
)
from src.services.risk_scoring_v2_orchestrator import (
    RiskScoringV2NotReadyError,
    RiskScoringV2Service,
)
from src.services.topology_preparation_service import (
    TopologyPreparationNotReadyError,
    prepare_topology_for_event,
)
from src.models.pipeline_checkpoint import (
    PIPELINE_PATTERN_DETECTION_V1,
)
from src.services.alert_execution_service import (
    execute_alert_generation,
)
from src.services.intelligence_pipeline_service import (
    execute_intelligence_pipeline,
)
from src.models.organization_work_schedule import (
    WORK_KIND_ALERT_RECONCILIATION,
    WORK_KIND_INTELLIGENCE,
)
from src.services.organization_work_schedule_service import (
    clear_organization_work_schedule,
    schedule_organization_work_retry,
)
from src.services.worker_operational_heartbeat import (
    WorkerOperationalHeartbeat,
)
from src.services.worker_operational_reporter import (
    WorkerOperationalReporter,
)
from src.services.worker_runtime_identity import (
    create_worker_instance_id,
)
from src.models.worker_operational_state import (
    WORKER_KIND_VECTOR,
)

from src.services.pipeline_source_discovery_service import (
    discover_alert_reconciliation_source,
    discover_pending_alert_reconciliation_sources,
    discover_pending_pipeline_sources,
    discover_pipeline_source,
)
from src.services.event_evidence_lock import (
    acquire_event_evidence_lock,
)
from src.services.lineage_invalidation_policy_service import (
    DEFAULT_LINEAGE_INVALIDATION_POLICY,
)
from src.services.temporal_lineage_invalidation_service import (
    TemporalLineageInvalidationNotReadyError,
    analyze_temporal_lineage_invalidation,
)

logger = logging.getLogger("semantic_drift.vector_worker")


DEFAULT_SNAPSHOT_LIMIT = 10
DEFAULT_EVENT_LIMIT = 10
DEFAULT_TEMPORAL_INVALIDATION_LIMIT = 10
DEFAULT_RISK_LIMIT = 10
DEFAULT_PATTERN_LIMIT = 10
DEFAULT_ANOMALY_LIMIT = 10
DEFAULT_ALERT_RECONCILIATION_LIMIT = 10
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_POLL_SECONDS = 10.0

RISK_NOT_READY_RETRY_SECONDS = 30
RISK_FAILURE_RETRY_SECONDS = 60
ORGANIZATION_WORK_FAILURE_RETRY_SECONDS = 60

EMBEDDING_STATUS_PENDING = "pending"
EMBEDDING_STATUS_FAILED = "failed"

VECTOR_STATUS_PENDING = "pending"
VECTOR_STATUS_FAILED = "failed"

RISK_STATUS_PENDING = "pending"
RISK_STATUS_FAILED = "failed"


@dataclass
class BatchStats:
    selected: int = 0
    completed: int = 0
    failed: int = 0
    deferred: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "selected": self.selected,
            "completed": self.completed,
            "failed": self.failed,
            "deferred": self.deferred,
        }


@dataclass
class WorkerCycleStats:
    snapshots: BatchStats
    events: BatchStats
    temporal_invalidation_events: BatchStats
    risk_events: BatchStats
    pattern_organizations: BatchStats
    anomaly_organizations: BatchStats
    alert_reconciliation_organizations: BatchStats = field(
        default_factory=BatchStats
    )


class VectorWorker:
    """
    Small PostgreSQL-backed orchestration worker.

    Domain services remain responsible for lifecycle state,
    attempts and persistence. This class only discovers eligible
    rows and invokes those services.
    """

    def __init__(
        self,
        *,
        snapshot_limit: int = DEFAULT_SNAPSHOT_LIMIT,
        event_limit: int = DEFAULT_EVENT_LIMIT,
        temporal_invalidation_limit: int = (
            DEFAULT_TEMPORAL_INVALIDATION_LIMIT
        ),
        risk_limit: int = DEFAULT_RISK_LIMIT,
        pattern_limit: int = DEFAULT_PATTERN_LIMIT,
        anomaly_limit: int = DEFAULT_ANOMALY_LIMIT,
        alert_reconciliation_limit: int = (
            DEFAULT_ALERT_RECONCILIATION_LIMIT
        ),
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        process_embeddings: bool = True,
        process_analysis: bool = True,
        process_temporal_invalidation: bool = True,
        process_risk_scoring: bool = True,
        process_pattern_detection: bool = True,
        process_anomaly_detection: bool = True,
        process_alert_reconciliation: bool = True,
    ) -> None:
        if snapshot_limit < 1:
            raise ValueError(
                "snapshot_limit must be at least 1."
            )

        if event_limit < 1:
            raise ValueError(
                "event_limit must be at least 1."
            )

        if temporal_invalidation_limit < 1:
            raise ValueError(
                "temporal_invalidation_limit must be at least 1."
            )

        if risk_limit < 1:
            raise ValueError(
                "risk_limit must be at least 1."
            )

        if pattern_limit < 1:
            raise ValueError(
                "pattern_limit must be at least 1."
            )

        if anomaly_limit < 1:
            raise ValueError(
                "anomaly_limit must be at least 1."
            )

        if alert_reconciliation_limit < 1:
            raise ValueError(
                "alert_reconciliation_limit must be at least 1."
            )

        if max_attempts < 1:
            raise ValueError(
                "max_attempts must be at least 1."
            )

        if (
            process_pattern_detection
            != process_anomaly_detection
        ):
            raise ValueError(
                "Pattern detection and anomaly detection must be "
                "enabled or disabled together because they execute "
                "inside the transactional intelligence pipeline."
            )

        if (
            not process_embeddings
            and not process_analysis
            and not process_temporal_invalidation
            and not process_risk_scoring
            and not process_pattern_detection
            and not process_anomaly_detection
            and not process_alert_reconciliation
        ):
            raise ValueError(
                "At least one processing stage must be enabled."
            )

        self.snapshot_limit = snapshot_limit
        self.event_limit = event_limit
        self.temporal_invalidation_limit = (
            temporal_invalidation_limit
        )
        self.risk_limit = risk_limit
        self.pattern_limit = pattern_limit
        self.anomaly_limit = anomaly_limit
        self.alert_reconciliation_limit = (
            alert_reconciliation_limit
        )
        self.max_attempts = max_attempts

        self.process_embeddings = process_embeddings
        self.process_analysis = process_analysis
        self.process_temporal_invalidation = (
            process_temporal_invalidation
        )
        self.process_risk_scoring = process_risk_scoring
        self.process_pattern_detection = (
            process_pattern_detection
        )
        self.process_anomaly_detection = (
            process_anomaly_detection
        )
        self.process_alert_reconciliation = (
            process_alert_reconciliation
        )

        self.embedding_service = (
            EmbeddingService()
            if self.process_embeddings
            else None
        )
        self.vector_analysis_service = (
            VectorAnalysisService()
            if self.process_analysis
            else None
        )
        self.risk_scoring_v2_service = (
            RiskScoringV2Service()
            if self.process_risk_scoring
            else None
        )
        self.risk_scoring_v3_shadow_service = (
            RiskScoringV3ShadowService()
            if self.process_risk_scoring
            else None
        )

    def _select_snapshot_ids(
        self,
        db: Session,
    ) -> list[str]:
        rows = (
            db.query(PageSnapshot.id)
            .filter(
                PageSnapshot.embedding_status.in_(
                    [
                        EMBEDDING_STATUS_PENDING,
                        EMBEDDING_STATUS_FAILED,
                    ]
                ),
                or_(
                    PageSnapshot.embedding_attempts.is_(None),
                    PageSnapshot.embedding_attempts
                    < self.max_attempts,
                ),
            )
            .order_by(
                PageSnapshot.crawled_at.asc(),
                PageSnapshot.id.asc(),
            )
            .limit(self.snapshot_limit)
            .all()
        )

        return [
            str(row[0])
            for row in rows
        ]

    def _select_event_ids(
        self,
        db: Session,
    ) -> list[str]:
        """
        Select vector-analysis events only when both snapshot
        embeddings are already completed.

        Filtering readiness here prevents old not-ready events
        from starving later ready events. VectorAnalysisService
        still validates both snapshots during processing.
        """
        old_snapshot = aliased(PageSnapshot)
        new_snapshot = aliased(PageSnapshot)

        rows = (
            db.query(
                SemanticDriftEvent.id
            )
            .join(
                old_snapshot,
                SemanticDriftEvent.old_snapshot_id
                == old_snapshot.id,
            )
            .join(
                new_snapshot,
                SemanticDriftEvent.new_snapshot_id
                == new_snapshot.id,
            )
            .filter(
                SemanticDriftEvent.vector_analysis_status.in_(
                    [
                        VECTOR_STATUS_PENDING,
                        VECTOR_STATUS_FAILED,
                    ]
                ),
                or_(
                    SemanticDriftEvent.vector_analysis_attempts.is_(None),
                    SemanticDriftEvent.vector_analysis_attempts
                    < self.max_attempts,
                ),
                old_snapshot.embedding_status == "completed",
                new_snapshot.embedding_status == "completed",
                old_snapshot.embedding.is_not(None),
                new_snapshot.embedding.is_not(None),
            )
            .order_by(
                SemanticDriftEvent.detected_at.asc(),
                SemanticDriftEvent.id.asc(),
            )
            .limit(self.event_limit)
            .all()
        )

        return [
            str(row[0])
            for row in rows
        ]


    def _select_temporal_invalidation_event_ids(
        self,
        db: Session,
    ) -> list[str]:
        """
        Select drift events whose semantic and vector evidence is
        complete and whose temporal invalidation analysis still needs
        to run.
        """
        rows = (
            db.query(
                SemanticDriftEvent.id
            )
            .filter(
                SemanticDriftEvent.analysis_status == "completed",
                SemanticDriftEvent.vector_analysis_status
                == VECTOR_ANALYSIS_STATUS_COMPLETED,
                SemanticDriftEvent.temporal_invalidation_status.in_(
                    [
                        TEMPORAL_INVALIDATION_STATUS_PENDING,
                        TEMPORAL_INVALIDATION_STATUS_FAILED,
                    ]
                ),
                SemanticDriftEvent.temporal_invalidation_attempts
                < self.max_attempts,
            )
            .order_by(
                SemanticDriftEvent.detected_at.asc(),
                SemanticDriftEvent.id.asc(),
            )
            .limit(
                self.temporal_invalidation_limit
            )
            .all()
        )

        return [
            str(row[0])
            for row in rows
        ]


    def _select_risk_event_ids(
        self,
        db: Session,
    ) -> list[str]:
        """
        Select vector-ready events missing a persisted deterministic
        Risk v2 assessment or Risk v3 shadow assessment.

        Durable versioned assessments are the source of truth for
        discovery rather than SemanticDriftEvent projection state.
        """
        existing_v2 = (
            db.query(
                RiskAssessment.id
            )
            .filter(
                RiskAssessment.organization_id
                == SemanticDriftEvent.organization_id,
                RiskAssessment.semantic_drift_event_id
                == SemanticDriftEvent.id,
                RiskAssessment.risk_version
                == RISK_VERSION_V2,
            )
        )

        existing_v3 = (
            db.query(
                RiskAssessment.id
            )
            .filter(
                RiskAssessment.organization_id
                == SemanticDriftEvent.organization_id,
                RiskAssessment.semantic_drift_event_id
                == SemanticDriftEvent.id,
                RiskAssessment.risk_version
                == RISK_VERSION_V3,
            )
        )

        rows = (
            db.query(
                SemanticDriftEvent.id
            )
            .filter(
                SemanticDriftEvent.analysis_status
                == "completed",
                SemanticDriftEvent.vector_analysis_status
                == "completed",
                SemanticDriftEvent.semantic_magnitude.is_not(
                    None
                ),
                or_(
                    SemanticDriftEvent.risk_retry_after.is_(None),
                    SemanticDriftEvent.risk_retry_after
                    <= datetime.now(timezone.utc),
                ),
                or_(
                    ~exists(
                        existing_v2.statement
                    ),
                    ~exists(
                        existing_v3.statement
                    ),
                ),
            )
            .order_by(
                SemanticDriftEvent.detected_at.asc(),
                SemanticDriftEvent.id.asc(),
            )
            .limit(
                self.risk_limit
            )
            .all()
        )

        return [
            str(row[0])
            for row in rows
        ]

    def process_snapshots(
        self,
    ) -> BatchStats:
        stats = BatchStats()

        discovery_db = SessionLocal()

        try:
            snapshot_ids = self._select_snapshot_ids(
                discovery_db
            )
        finally:
            discovery_db.close()

        stats.selected = len(snapshot_ids)

        if not snapshot_ids:
            logger.info(
                "No eligible snapshots found."
            )
            return stats

        logger.info(
            "Selected %s snapshot(s) for embedding.",
            len(snapshot_ids),
        )

        for index, snapshot_id in enumerate(
            snapshot_ids
        ):
            db = SessionLocal()

            try:
                snapshot = (
                    db.query(PageSnapshot)
                    .filter(
                        PageSnapshot.id
                        == snapshot_id
                    )
                    .one_or_none()
                )

                if snapshot is None:
                    logger.warning(
                        "Snapshot disappeared before processing: %s",
                        snapshot_id,
                    )
                    stats.deferred += 1
                    continue

                if snapshot.embedding_status not in {
                    EMBEDDING_STATUS_PENDING,
                    EMBEDDING_STATUS_FAILED,
                }:
                    logger.info(
                        "Snapshot is no longer eligible: "
                        "snapshot=%s status=%s",
                        snapshot.id,
                        snapshot.embedding_status,
                    )
                    stats.deferred += 1
                    continue

                attempts = int(
                    snapshot.embedding_attempts
                    or 0
                )

                if attempts >= self.max_attempts:
                    logger.info(
                        "Snapshot reached maximum attempts: "
                        "snapshot=%s attempts=%s",
                        snapshot.id,
                        attempts,
                    )
                    stats.deferred += 1
                    continue

                logger.info(
                    "Embedding snapshot: "
                    "snapshot=%s attempt=%s/%s",
                    snapshot.id,
                    attempts + 1,
                    self.max_attempts,
                )

                self.embedding_service.embed_snapshot(
                    db,
                    snapshot,
                )

                stats.completed += 1

                logger.info(
                    "Snapshot embedding completed: %s",
                    snapshot.id,
                )

            except EmbeddingProviderUnavailableError as exc:
                stats.failed += 1

                remaining = (
                    len(snapshot_ids)
                    - index
                    - 1
                )

                stats.deferred += remaining

                logger.error(
                    "Embedding provider circuit opened: "
                    "snapshot=%s reason=%s "
                    "remaining_deferred=%s",
                    snapshot_id,
                    exc,
                    remaining,
                )

                try:
                    db.rollback()
                except Exception:
                    logger.exception(
                        "Snapshot rollback failed after "
                        "provider outage: %s",
                        snapshot_id,
                    )

                break

            except Exception:
                stats.failed += 1

                logger.exception(
                    "Snapshot embedding failed in worker: %s",
                    snapshot_id,
                )

                try:
                    db.rollback()
                except Exception:
                    logger.exception(
                        "Snapshot rollback failed: %s",
                        snapshot_id,
                    )

            finally:
                db.close()

        return stats

    def process_events(
        self,
    ) -> BatchStats:
        stats = BatchStats()

        discovery_db = SessionLocal()

        try:
            event_ids = self._select_event_ids(
                discovery_db
            )
        finally:
            discovery_db.close()

        stats.selected = len(event_ids)

        if not event_ids:
            logger.info(
                "No eligible drift events found."
            )
            return stats

        logger.info(
            "Selected %s drift event(s) for vector analysis.",
            len(event_ids),
        )

        for event_id in event_ids:
            db = SessionLocal()

            try:
                event = (
                    db.query(SemanticDriftEvent)
                    .filter(
                        SemanticDriftEvent.id == event_id
                    )
                    .one_or_none()
                )

                if event is None:
                    logger.warning(
                        "Drift event disappeared before processing: %s",
                        event_id,
                    )
                    stats.deferred += 1
                    continue

                if event.vector_analysis_status not in {
                    VECTOR_STATUS_PENDING,
                    VECTOR_STATUS_FAILED,
                }:
                    logger.info(
                        "Drift event is no longer eligible: "
                        "event=%s status=%s",
                        event.id,
                        event.vector_analysis_status,
                    )
                    stats.deferred += 1
                    continue

                attempts = int(
                    event.vector_analysis_attempts
                    or 0
                )

                if attempts >= self.max_attempts:
                    logger.info(
                        "Drift event reached maximum attempts: "
                        "event=%s attempts=%s",
                        event.id,
                        attempts,
                    )
                    stats.deferred += 1
                    continue

                logger.info(
                    "Analyzing drift event: "
                    "event=%s attempt=%s/%s",
                    event.id,
                    attempts + 1,
                    self.max_attempts,
                )

                try:
                    result = (
                        self.vector_analysis_service
                        .analyze_event(
                            db,
                            event,
                        )
                    )
                except VectorAnalysisNotReadyError:
                    stats.deferred += 1

                    logger.info(
                        "Drift event deferred because snapshot "
                        "embeddings are not ready: %s",
                        event.id,
                    )
                    continue

                if (
                    result.vector_analysis_status
                    == "completed"
                ):
                    stats.completed += 1

                    logger.info(
                        "Vector analysis completed: %s",
                        result.id,
                    )

                elif (
                    result.vector_analysis_status
                    == "pending"
                ):
                    stats.deferred += 1

                    logger.info(
                        "Vector analysis remains pending: %s",
                        result.id,
                    )

                elif (
                    result.vector_analysis_status
                    == "failed"
                ):
                    stats.failed += 1

                    logger.warning(
                        "Vector analysis marked failed: %s",
                        result.id,
                    )

                else:
                    stats.deferred += 1

                    logger.info(
                        "Vector analysis returned status=%s: %s",
                        result.vector_analysis_status,
                        result.id,
                    )

            except Exception:
                stats.failed += 1

                logger.exception(
                    "Vector analysis failed in worker: %s",
                    event_id,
                )

                try:
                    db.rollback()
                except Exception:
                    logger.exception(
                        "Vector analysis rollback failed: %s",
                        event_id,
                    )

            finally:
                db.close()

        return stats

    def process_temporal_invalidation_events(
        self,
    ) -> BatchStats:
        stats = BatchStats()

        discovery_db = SessionLocal()

        try:
            event_ids = (
                self._select_temporal_invalidation_event_ids(
                    discovery_db
                )
            )
        finally:
            discovery_db.close()

        stats.selected = len(event_ids)

        if not event_ids:
            logger.info(
                "No eligible temporal invalidation events found."
            )
            return stats

        logger.info(
            "Selected %s drift event(s) for temporal invalidation.",
            len(event_ids),
        )

        for event_id in event_ids:
            db = SessionLocal()

            try:
                event = (
                    db.query(
                        SemanticDriftEvent
                    )
                    .filter(
                        SemanticDriftEvent.id == event_id
                    )
                    .one_or_none()
                )

                if event is None:
                    logger.warning(
                        "Temporal invalidation event disappeared "
                        "before processing: %s",
                        event_id,
                    )
                    stats.deferred += 1
                    continue

                organization_id = event.organization_id

                acquire_event_evidence_lock(
                    db,
                    organization_id=organization_id,
                    event_id=event.id,
                )

                db.expire_all()

                event = (
                    db.query(
                        SemanticDriftEvent
                    )
                    .filter(
                        SemanticDriftEvent.id == event_id,
                        SemanticDriftEvent.organization_id
                        == organization_id,
                    )
                    .one_or_none()
                )

                if event is None:
                    logger.warning(
                        "Temporal invalidation event disappeared "
                        "after lock acquisition: %s",
                        event_id,
                    )
                    stats.deferred += 1
                    continue

                if (
                    event.analysis_status != "completed"
                    or event.vector_analysis_status
                    != VECTOR_ANALYSIS_STATUS_COMPLETED
                ):
                    logger.info(
                        "Temporal invalidation event is no longer "
                        "ready: %s",
                        event.id,
                    )
                    stats.deferred += 1
                    continue

                if (
                    event.temporal_invalidation_status
                    == TEMPORAL_INVALIDATION_STATUS_COMPLETED
                ):
                    logger.info(
                        "Temporal invalidation already completed: %s",
                        event.id,
                    )
                    continue

                if (
                    event.temporal_invalidation_status
                    not in {
                        TEMPORAL_INVALIDATION_STATUS_PENDING,
                        TEMPORAL_INVALIDATION_STATUS_FAILED,
                    }
                    or event.temporal_invalidation_attempts
                    >= self.max_attempts
                ):
                    logger.info(
                        "Temporal invalidation event is no longer "
                        "eligible: %s",
                        event.id,
                    )
                    stats.deferred += 1
                    continue

                event.temporal_invalidation_attempts += 1
                event.temporal_invalidation_error = None

                result = analyze_temporal_lineage_invalidation(
                    db,
                    organization_id=event.organization_id,
                    event_id=event.id,
                    policy_identity=DEFAULT_LINEAGE_INVALIDATION_POLICY,
                )

                event.temporal_invalidation_status = (
                    TEMPORAL_INVALIDATION_STATUS_COMPLETED
                )
                event.temporal_invalidation_error = None
                event.temporal_invalidation_analyzed_at = (
                    datetime.now(timezone.utc)
                )

                db.commit()

                stats.completed += 1

                logger.info(
                    "Temporal invalidation completed: "
                    "event=%s roots=%s analyses=%s",
                    event_id,
                    len(result.roots),
                    len(result.analyses),
                )

            except TemporalLineageInvalidationNotReadyError as exc:
                try:
                    db.rollback()
                except Exception:
                    logger.exception(
                        "Temporal invalidation rollback failed: %s",
                        event_id,
                    )

                stats.deferred += 1

                logger.info(
                    "Temporal invalidation became not ready: "
                    "event=%s error=%s",
                    event_id,
                    exc,
                )

            except Exception as exc:
                try:
                    db.rollback()
                except Exception:
                    logger.exception(
                        "Temporal invalidation rollback failed: %s",
                        event_id,
                    )

                failure_db = SessionLocal()

                try:
                    failure_event = (
                        failure_db.query(
                            SemanticDriftEvent
                        )
                        .filter(
                            SemanticDriftEvent.id == event_id
                        )
                        .one_or_none()
                    )

                    if failure_event is not None:
                        failure_organization_id = (
                            failure_event.organization_id
                        )

                        acquire_event_evidence_lock(
                            failure_db,
                            organization_id=(
                                failure_organization_id
                            ),
                            event_id=failure_event.id,
                        )

                        failure_db.expire_all()

                        failure_event = (
                            failure_db.query(
                                SemanticDriftEvent
                            )
                            .filter(
                                SemanticDriftEvent.id == event_id,
                                SemanticDriftEvent.organization_id
                                == failure_organization_id,
                            )
                            .one_or_none()
                        )

                        if (
                            failure_event is not None
                            and
                            failure_event.temporal_invalidation_status
                            != TEMPORAL_INVALIDATION_STATUS_COMPLETED
                            and
                            failure_event.temporal_invalidation_attempts
                            < self.max_attempts
                        ):
                            failure_event.temporal_invalidation_attempts += 1
                            failure_event.temporal_invalidation_status = (
                                TEMPORAL_INVALIDATION_STATUS_FAILED
                            )
                            failure_event.temporal_invalidation_error = str(
                                exc
                            )

                            failure_db.commit()
                        else:
                            failure_db.rollback()

                except Exception:
                    try:
                        failure_db.rollback()
                    except Exception:
                        logger.exception(
                            "Temporal invalidation failure-state "
                            "rollback failed: %s",
                            event_id,
                        )

                    logger.exception(
                        "Could not persist temporal invalidation "
                        "failure state: %s",
                        event_id,
                    )

                finally:
                    failure_db.close()

                stats.failed += 1

                logger.exception(
                    "Temporal invalidation failed in worker: %s",
                    event_id,
                )

            finally:
                db.close()

        return stats

    def process_risk_events(
        self,
    ) -> BatchStats:
        stats = BatchStats()

        discovery_db = SessionLocal()

        try:
            event_ids = self._select_risk_event_ids(
                discovery_db
            )
        finally:
            discovery_db.close()

        stats.selected = len(event_ids)

        if not event_ids:
            logger.info(
                "No eligible deterministic risk events found."
            )
            return stats

        logger.info(
            "Selected %s drift event(s) for deterministic risk.",
            len(event_ids),
        )

        for event_id in event_ids:
            db = SessionLocal()

            try:
                event = (
                    db.query(
                        SemanticDriftEvent
                    )
                    .filter(
                        SemanticDriftEvent.id
                        == event_id
                    )
                    .one_or_none()
                )

                if event is None:
                    logger.warning(
                        "Risk event disappeared "
                        "before processing: %s",
                        event_id,
                    )
                    stats.deferred += 1
                    continue

                if (
                    event.analysis_status
                    != "completed"
                    or event.vector_analysis_status
                    != "completed"
                    or event.semantic_magnitude
                    is None
                ):
                    logger.info(
                        "Risk event is no longer "
                        "vector-ready: %s",
                        event.id,
                    )
                    stats.deferred += 1
                    continue

                acquire_event_evidence_lock(
                    db,
                    organization_id=event.organization_id,
                    event_id=event.id,
                )

                existing_versions = {
                    row[0]
                    for row in (
                        db.query(
                            RiskAssessment.risk_version
                        )
                        .filter(
                            RiskAssessment.organization_id
                            == event.organization_id,
                            RiskAssessment
                            .semantic_drift_event_id
                            == event.id,
                            RiskAssessment.risk_version.in_(
                                [
                                    RISK_VERSION_V2,
                                    RISK_VERSION_V3,
                                ]
                            ),
                        )
                        .all()
                    )
                }

                needs_v2 = (
                    RISK_VERSION_V2
                    not in existing_versions
                )
                needs_v3 = (
                    RISK_VERSION_V3
                    not in existing_versions
                )

                if not needs_v2 and not needs_v3:
                    logger.info(
                        "Deterministic risk assessments "
                        "already persisted: %s",
                        event.id,
                    )
                    continue

                v2_result = None
                v3_result = None

                if needs_v2:
                    logger.info(
                        "Preparing topology for "
                        "Risk v2 event=%s",
                        event.id,
                    )

                    prepare_topology_for_event(
                        db,
                        event=event,
                    )

                    logger.info(
                        "Scoring Risk v2 event=%s",
                        event.id,
                    )

                    v2_result = (
                        self.risk_scoring_v2_service
                        .score_event(
                            db,
                            event,
                        )
                    )

                if needs_v3:
                    logger.info(
                        "Scoring Risk v3 shadow "
                        "event=%s",
                        event.id,
                    )

                    v3_result = (
                        self.risk_scoring_v3_shadow_service
                        .score_event(
                            db,
                            event,
                        )
                    )

                event.risk_retry_after = None

                db.commit()

                stats.completed += 1

                logger.info(
                    "Deterministic risk completed: "
                    "event=%s v2_created=%s "
                    "v3_created=%s",
                    event.id,
                    (
                        v2_result.assessment_created
                        if v2_result is not None
                        else False
                    ),
                    (
                        v3_result.assessment_created
                        if v3_result is not None
                        else False
                    ),
                )

            except (
                TopologyPreparationNotReadyError,
                RiskScoringV2NotReadyError,
                RiskScoringV3ShadowNotReadyError,
            ) as exc:
                db.rollback()

                retry_event = (
                    db.query(
                        SemanticDriftEvent
                    )
                    .filter(
                        SemanticDriftEvent.id
                        == event_id
                    )
                    .one_or_none()
                )

                if retry_event is not None:
                    retry_event.risk_retry_after = (
                        datetime.now(timezone.utc)
                        + timedelta(
                            seconds=(
                                RISK_NOT_READY_RETRY_SECONDS
                            )
                        )
                    )
                    db.commit()

                stats.deferred += 1

                logger.info(
                    "Deterministic risk deferred: "
                    "event=%s reason=%s",
                    event_id,
                    exc,
                )

            except Exception:
                db.rollback()

                retry_event = (
                    db.query(
                        SemanticDriftEvent
                    )
                    .filter(
                        SemanticDriftEvent.id
                        == event_id
                    )
                    .one_or_none()
                )

                if retry_event is not None:
                    retry_event.risk_retry_after = (
                        datetime.now(timezone.utc)
                        + timedelta(
                            seconds=(
                                RISK_FAILURE_RETRY_SECONDS
                            )
                        )
                    )
                    db.commit()

                stats.failed += 1

                logger.exception(
                    "Deterministic risk failed "
                    "in worker: %s",
                    event_id,
                )

            finally:
                db.close()

        return stats

    def process_intelligence_organizations(
        self,
    ) -> BatchStats:
        """
        Process organization intelligence atomically.

        Pattern detection, Pattern checkpoint persistence,
        Anomaly detection, Alert generation and Alert checkpoint
        persistence all execute inside one database transaction.

        A failure in any downstream stage rolls the entire
        organization execution back so no upstream checkpoint can
        advance independently.
        """
        stats = BatchStats()

        discovery_db = SessionLocal()

        try:
            pending = discover_pending_pipeline_sources(
                discovery_db,
                pipeline_name=PIPELINE_PATTERN_DETECTION_V1,
                limit=min(
                    self.pattern_limit,
                    self.anomaly_limit,
                ),
            )
        finally:
            discovery_db.close()

        stats.selected = len(pending)

        if not pending:
            logger.info(
                "No organizations pending intelligence processing."
            )
            return stats

        logger.info(
            "Selected %s organization(s) for intelligence processing.",
            len(pending),
        )

        for candidate in pending:
            organization_id = candidate.organization_id
            db = SessionLocal()

            try:
                # Re-discover inside the processing transaction.
                # Another worker may have advanced the checkpoint
                # after the discovery batch was selected.
                source = discover_pipeline_source(
                    db,
                    organization_id=organization_id,
                    pipeline_name=PIPELINE_PATTERN_DETECTION_V1,
                )

                if source is None:
                    stats.deferred += 1

                    logger.info(
                        "Intelligence organization is no longer "
                        "pending: %s",
                        organization_id,
                    )
                    continue

                result = execute_intelligence_pipeline(
                    db,
                    source=source,
                )

                clear_organization_work_schedule(
                    db,
                    organization_id=organization_id,
                    work_kind=WORK_KIND_INTELLIGENCE,
                )

                db.commit()

                stats.completed += 1

                logger.info(
                    "Intelligence processing completed: "
                    "organization=%s "
                    "patterns_detected=%s "
                    "patterns_created=%s "
                    "patterns_existing=%s "
                    "anomalies_detected=%s "
                    "anomalies_created=%s "
                    "anomalies_existing=%s "
                    "alerts_created=%s "
                    "alerts_existing=%s "
                    "pattern_checkpoint_advanced=%s "
                    "alert_checkpoint_advanced=%s",
                    organization_id,
                    result.pattern.detected_count,
                    result.pattern.created_count,
                    result.pattern.existing_count,
                    result.anomaly.detected_count,
                    result.anomaly.created_count,
                    result.anomaly.existing_count,
                    result.alert.alert_created_count,
                    result.alert.alert_existing_count,
                    result.pattern_checkpoint.advanced,
                    result.alert.checkpoint.advanced,
                )

            except Exception:
                db.rollback()

                stats.failed += 1

                logger.exception(
                    "Intelligence processing failed in worker: "
                    "organization=%s",
                    organization_id,
                )

                retry_db = SessionLocal()

                try:
                    pending_source = discover_pipeline_source(
                        retry_db,
                        organization_id=organization_id,
                        pipeline_name=PIPELINE_PATTERN_DETECTION_V1,
                    )

                    if pending_source is not None:
                        schedule_organization_work_retry(
                            retry_db,
                            organization_id=organization_id,
                            work_kind=WORK_KIND_INTELLIGENCE,
                            retry_seconds=(
                                ORGANIZATION_WORK_FAILURE_RETRY_SECONDS
                            ),
                        )

                    retry_db.commit()

                except Exception:
                    retry_db.rollback()

                    logger.exception(
                        "Failed to persist intelligence retry schedule: "
                        "organization=%s",
                        organization_id,
                    )

                finally:
                    retry_db.close()

            finally:
                db.close()

        return stats

    def process_alert_reconciliation_organizations(
        self,
    ) -> BatchStats:
        """
        Process explicit Action Layer reconciliation work.

        This stage is intentionally independent from new Risk v2
        materialization. Watchlist policy changes and newly-created webhook
        subscriptions must be reconciled even when an organization produces
        no subsequent knowledge event.

        Candidate discovery uses Action Layer pending state. The organization
        is re-discovered inside the processing transaction so stale candidates
        cannot execute after another worker has already completed the work.

        Alert generation owns no transaction boundary. Commit and rollback
        remain worker responsibilities.
        """
        stats = BatchStats()

        discovery_db = SessionLocal()

        try:
            pending = (
                discover_pending_alert_reconciliation_sources(
                    discovery_db,
                    limit=self.alert_reconciliation_limit,
                )
            )
        finally:
            discovery_db.close()

        stats.selected = len(
            pending
        )

        if not pending:
            logger.info(
                "No organizations pending Alert reconciliation."
            )
            return stats

        logger.info(
            "Selected %s organization(s) for Alert reconciliation.",
            len(pending),
        )

        for candidate in pending:
            organization_id = (
                candidate.organization_id
            )
            db = SessionLocal()

            try:
                source = (
                    discover_alert_reconciliation_source(
                        db,
                        organization_id=organization_id,
                    )
                )

                if source is None:
                    stats.deferred += 1

                    logger.info(
                        "Alert reconciliation organization is no "
                        "longer pending: %s",
                        organization_id,
                    )
                    continue

                result = execute_alert_generation(
                    db,
                    source=source,
                )

                clear_organization_work_schedule(
                    db,
                    organization_id=organization_id,
                    work_kind=WORK_KIND_ALERT_RECONCILIATION,
                )

                db.commit()

                stats.completed += 1

                logger.info(
                    "Alert reconciliation completed: "
                    "organization=%s "
                    "risk_sources=%s "
                    "pattern_sources=%s "
                    "anomaly_sources=%s "
                    "alerts_created=%s "
                    "alerts_existing=%s "
                    "alert_checkpoint_advanced=%s",
                    organization_id,
                    result.risk_source_count,
                    result.pattern_source_count,
                    result.anomaly_source_count,
                    result.alert_created_count,
                    result.alert_existing_count,
                    result.checkpoint.advanced,
                )

            except Exception:
                db.rollback()

                stats.failed += 1

                logger.exception(
                    "Alert reconciliation failed in worker: "
                    "organization=%s",
                    organization_id,
                )

                retry_db = SessionLocal()

                try:
                    pending_source = (
                        discover_alert_reconciliation_source(
                            retry_db,
                            organization_id=organization_id,
                        )
                    )

                    if pending_source is not None:
                        schedule_organization_work_retry(
                            retry_db,
                            organization_id=organization_id,
                            work_kind=(
                                WORK_KIND_ALERT_RECONCILIATION
                            ),
                            retry_seconds=(
                                ORGANIZATION_WORK_FAILURE_RETRY_SECONDS
                            ),
                        )

                    retry_db.commit()

                except Exception:
                    retry_db.rollback()

                    logger.exception(
                        "Failed to persist Alert reconciliation "
                        "retry schedule: organization=%s",
                        organization_id,
                    )

                finally:
                    retry_db.close()

            finally:
                db.close()

        return stats

    def run_cycle(
        self,
    ) -> WorkerCycleStats:
        if self.process_embeddings:
            snapshot_stats = (
                self.process_snapshots()
            )
        else:
            snapshot_stats = BatchStats()

        if self.process_analysis:
            event_stats = self.process_events()
        else:
            event_stats = BatchStats()

        if self.process_temporal_invalidation:
            temporal_invalidation_stats = (
                self.process_temporal_invalidation_events()
            )
        else:
            temporal_invalidation_stats = BatchStats()

        if self.process_risk_scoring:
            risk_stats = (
                self.process_risk_events()
            )
        else:
            risk_stats = BatchStats()

        if (
            self.process_pattern_detection
            and self.process_anomaly_detection
        ):
            intelligence_stats = (
                self.process_intelligence_organizations()
            )

            # Preserve the existing WorkerCycleStats surface while
            # Pattern and Anomaly now share one transactional batch.
            pattern_stats = intelligence_stats
            anomaly_stats = intelligence_stats
        else:
            pattern_stats = BatchStats()
            anomaly_stats = BatchStats()

        if self.process_alert_reconciliation:
            alert_reconciliation_stats = (
                self.process_alert_reconciliation_organizations()
            )
        else:
            alert_reconciliation_stats = BatchStats()

        logger.info(
            "Vector worker cycle complete: "
            "snapshots=%s events=%s "
            "temporal_invalidation_events=%s "
            "risk_events=%s "
            "pattern_organizations=%s "
            "anomaly_organizations=%s "
            "alert_reconciliation_organizations=%s",
            snapshot_stats.as_dict(),
            event_stats.as_dict(),
            temporal_invalidation_stats.as_dict(),
            risk_stats.as_dict(),
            pattern_stats.as_dict(),
            anomaly_stats.as_dict(),
            alert_reconciliation_stats.as_dict(),
        )

        return WorkerCycleStats(
            snapshots=snapshot_stats,
            events=event_stats,
            temporal_invalidation_events=(
                temporal_invalidation_stats
            ),
            risk_events=risk_stats,
            pattern_organizations=pattern_stats,
            anomaly_organizations=anomaly_stats,
            alert_reconciliation_organizations=(
                alert_reconciliation_stats
            ),
        )

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Process snapshot embeddings, vector analysis, "
            "risk scoring and transactional intelligence "
            "detection."
        )
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "Run one processing cycle and exit."
        ),
    )

    parser.add_argument(
        "--snapshot-limit",
        type=int,
        default=DEFAULT_SNAPSHOT_LIMIT,
        help=(
            "Maximum snapshots processed per cycle."
        ),
    )

    parser.add_argument(
        "--event-limit",
        type=int,
        default=DEFAULT_EVENT_LIMIT,
        help=(
            "Maximum vector-analysis events processed per cycle."
        ),
    )

    parser.add_argument(
        "--temporal-invalidation-limit",
        type=int,
        default=DEFAULT_TEMPORAL_INVALIDATION_LIMIT,
        help=(
            "Maximum temporal-invalidation events processed "
            "per cycle."
        ),
    )

    parser.add_argument(
        "--risk-limit",
        type=int,
        default=DEFAULT_RISK_LIMIT,
        help=(
            "Maximum risk-scoring events processed per cycle."
        ),
    )

    parser.add_argument(
        "--pattern-limit",
        type=int,
        default=DEFAULT_PATTERN_LIMIT,
        help=(
            "Maximum organizations processed by pattern "
            "detection per cycle."
        ),
    )

    parser.add_argument(
        "--anomaly-limit",
        type=int,
        default=DEFAULT_ANOMALY_LIMIT,
        help=(
            "Maximum organizations processed by anomaly "
            "detection per cycle."
        ),
    )

    parser.add_argument(
        "--alert-reconciliation-limit",
        type=int,
        default=DEFAULT_ALERT_RECONCILIATION_LIMIT,
        help=(
            "Maximum organizations processed by Alert "
            "reconciliation per cycle."
        ),
    )

    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=(
            "Maximum service attempts per item."
        ),
    )

    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help=(
            "Seconds between cycles in continuous mode."
        ),
    )

    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help=(
            "Do not process snapshot embeddings."
        ),
    )

    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help=(
            "Do not process drift-event vector analysis."
        ),
    )

    parser.add_argument(
        "--skip-temporal-invalidation",
        action="store_true",
        help=(
            "Do not process temporal lineage invalidation analysis."
        ),
    )

    parser.add_argument(
        "--skip-risk-scoring",
        action="store_true",
        help=(
            "Do not process drift-event risk scoring."
        ),
    )

    parser.add_argument(
        "--skip-pattern-detection",
        action="store_true",
        help=(
            "Do not process organization pattern detection."
        ),
    )

    parser.add_argument(
        "--skip-anomaly-detection",
        action="store_true",
        help=(
            "Do not process organization anomaly detection."
        ),
    )

    parser.add_argument(
        "--skip-alert-reconciliation",
        action="store_true",
        help=(
            "Do not process Alert reconciliation."
        ),
    )

    return parser


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(name)s: "
            "%(message)s"
        ),
    )


def main() -> int:
    configure_logging()

    parser = build_parser()
    args = parser.parse_args()

    if args.poll_seconds <= 0:
        parser.error(
            "--poll-seconds must be greater than 0."
        )

    try:
        worker = VectorWorker(
            snapshot_limit=args.snapshot_limit,
            event_limit=args.event_limit,
            temporal_invalidation_limit=(
                args.temporal_invalidation_limit
            ),
            risk_limit=args.risk_limit,
            pattern_limit=args.pattern_limit,
            anomaly_limit=args.anomaly_limit,
            alert_reconciliation_limit=(
                args.alert_reconciliation_limit
            ),
            max_attempts=args.max_attempts,
            process_embeddings=(
                not args.skip_embeddings
            ),
            process_analysis=(
                not args.skip_analysis
            ),
            process_temporal_invalidation=(
                not args.skip_temporal_invalidation
            ),
            process_risk_scoring=(
                not args.skip_risk_scoring
            ),
            process_pattern_detection=(
                not args.skip_pattern_detection
            ),
            process_anomaly_detection=(
                not args.skip_anomaly_detection
            ),
            process_alert_reconciliation=(
                not args.skip_alert_reconciliation
            ),
        )
    except ValueError as exc:
        parser.error(
            str(exc)
        )

    reporter = WorkerOperationalReporter(
        worker_kind=WORKER_KIND_VECTOR,
        worker_instance_id=create_worker_instance_id(),
    )
    reporter.started()

    heartbeat = WorkerOperationalHeartbeat(
        reporter.heartbeat,
    )
    heartbeat.start()

    stop_requested = False

    def request_stop(
        signum,
        frame,
    ) -> None:
        del frame

        nonlocal stop_requested

        stop_requested = True

        logger.info(
            "Shutdown requested by signal %s.",
            signum,
        )

    signal.signal(
        signal.SIGINT,
        request_stop,
    )

    if hasattr(
        signal,
        "SIGTERM",
    ):
        signal.signal(
            signal.SIGTERM,
            request_stop,
        )

    if args.once:
        try:
            reporter.cycle_started()

            try:
                worker.run_cycle()
            except Exception as exc:
                reporter.cycle_failed(exc)
                raise

            reporter.cycle_succeeded()
            heartbeat.raise_if_failed()
            return 0
        finally:
            heartbeat.stop()

    try:
        logger.info(
            "Vector worker started: "
            "snapshot_limit=%s "
            "event_limit=%s "
            "temporal_invalidation_limit=%s "
            "risk_limit=%s "
            "max_attempts=%s "
            "poll_seconds=%s",
            args.snapshot_limit,
            args.event_limit,
            args.temporal_invalidation_limit,
            args.risk_limit,
            args.max_attempts,
            args.poll_seconds,
        )

        while not stop_requested:
            heartbeat.raise_if_failed()
            reporter.cycle_started()

            try:
                worker.run_cycle()
            except Exception as exc:
                reporter.cycle_failed(exc)

                logger.exception(
                    "Unhandled vector worker cycle failure."
                )
            else:
                reporter.cycle_succeeded()

            if stop_requested:
                break

            sleep_remaining = float(
                args.poll_seconds
            )

            while (
                sleep_remaining > 0
                and not stop_requested
            ):
                heartbeat.raise_if_failed()

                sleep_for = min(
                    1.0,
                    sleep_remaining,
                )

                time.sleep(
                    sleep_for
                )

                sleep_remaining -= sleep_for

        heartbeat.raise_if_failed()

        logger.info(
            "Vector worker stopped."
        )

        return 0
    finally:
        heartbeat.stop()


if __name__ == "__main__":
    sys.exit(
        main()
    )