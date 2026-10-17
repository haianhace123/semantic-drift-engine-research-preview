"""
Transactional production orchestration for downstream intelligence stages.

Flow:

    deterministic Risk v2 source watermark
    -> Pattern detection + durable Pattern checkpoint
    -> Anomaly detection
    -> Alert generation + durable Alert checkpoint

Correctness invariants:

- Pattern and Alert consume the same Risk v2 materialization watermark.
- Pattern completes before Alert is allowed to advance its checkpoint.
- Anomaly completes before Alert generation begins.
- Anomaly does not yet advance PIPELINE_ANOMALY_DETECTION_V1 because its
  SemanticDriftEvent evidence currently has effective-time ordering only and
  no independent materialization-time knowledge cutoff.
- no commit;
- no rollback;
- no external delivery;
- no Slack/Webhook calls.

The caller owns the surrounding transaction. Therefore a failure in Pattern,
Anomaly, or Alert can be rolled back together with every checkpoint and
persistence mutation performed by this orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid

from sqlalchemy.orm import Session

from src.models.pipeline_checkpoint import (
    PIPELINE_ALERT_GENERATION_V1,
    PIPELINE_PATTERN_DETECTION_V1,
)
from src.services.alert_execution_service import (
    AlertExecutionResult,
    execute_alert_generation,
)
from src.services.anomaly_execution_service import (
    AnomalyExecutionResult,
    execute_anomaly_detection,
)
from src.services.pattern_execution_service import (
    PatternExecutionResult,
    execute_pattern_detection,
)
from src.services.organization_execution_lock_service import (
    acquire_organization_execution_lock,
)
from src.services.pipeline_checkpoint_service import (
    PipelineCheckpointResult,
    persist_pipeline_checkpoint,
)
from src.services.pipeline_source_discovery_service import (
    PipelineSourceDiscovery,
)


class IntelligencePipelineExecutionError(
    RuntimeError
):
    """Raised when an intelligence pipeline source is inconsistent."""


@dataclass(frozen=True)
class IntelligencePipelineExecutionResult:
    organization_id: uuid.UUID
    source_created_at: datetime
    source_record_id: uuid.UUID
    temporal_cursor: datetime

    pattern: PatternExecutionResult
    pattern_checkpoint: PipelineCheckpointResult

    anomaly: AnomalyExecutionResult

    alert: AlertExecutionResult


def _alert_source(
    source: PipelineSourceDiscovery,
) -> PipelineSourceDiscovery:
    """
    Rebind one discovered Risk source watermark to the Alert stage.

    PipelineSourceDiscovery.pipeline_name identifies the consuming stage.
    The upstream Risk materialization position and temporal cursor must remain
    identical across Pattern -> Anomaly -> Alert execution.
    """
    return PipelineSourceDiscovery(
        organization_id=source.organization_id,
        pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        source_created_at=source.source_created_at,
        source_record_id=source.source_record_id,
        temporal_cursor=source.temporal_cursor,
    )


def _validate_source(
    source: PipelineSourceDiscovery,
) -> None:
    if (
        source.pipeline_name
        != PIPELINE_PATTERN_DETECTION_V1
    ):
        raise IntelligencePipelineExecutionError(
            "PipelineSourceDiscovery pipeline_name does not "
            f"match {PIPELINE_PATTERN_DETECTION_V1!r}."
        )


def execute_intelligence_pipeline(
    db: Session,
    *,
    source: PipelineSourceDiscovery,
) -> IntelligencePipelineExecutionResult:
    """
    Execute Pattern -> Anomaly -> Alert for one pending organization.

    `source` must be discovered for PIPELINE_PATTERN_DETECTION_V1.

    Pattern evidence is knowledge-gated by the exact Risk v2 source tuple:

        (source_created_at, source_record_id)

    Pattern checkpoint advancement happens only after Pattern persistence
    succeeds.

    Anomaly currently runs using the monotonic effective-time cursor but does
    not receive a durable materialization checkpoint. This is intentional
    until Anomaly evidence has a knowledge-time/materialization cutoff.

    Alert executes last. Therefore its durable checkpoint cannot advance
    unless both Pattern and Anomaly execution have completed successfully.

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

    pattern_result = (
        execute_pattern_detection(
            db,
            organization_id=(
                source.organization_id
            ),
            as_of=(
                source.temporal_cursor
            ),
            knowledge_cutoff=(
                source.source_created_at
            ),
            knowledge_cutoff_record_id=(
                source.source_record_id
            ),
        )
    )

    pattern_checkpoint = (
        persist_pipeline_checkpoint(
            db,
            organization_id=(
                source.organization_id
            ),
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
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
                "detected_count": (
                    pattern_result.detected_count
                ),
                "created_count": (
                    pattern_result.created_count
                ),
                "existing_count": (
                    pattern_result.existing_count
                ),
            },
        )
    )

    anomaly_result = (
        execute_anomaly_detection(
            db,
            organization_id=(
                source.organization_id
            ),
            as_of=(
                source.temporal_cursor
            ),
        )
    )

    alert_result = (
        execute_alert_generation(
            db,
            source=_alert_source(
                source
            ),
        )
    )

    return IntelligencePipelineExecutionResult(
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
        pattern=pattern_result,
        pattern_checkpoint=(
            pattern_checkpoint
        ),
        anomaly=anomaly_result,
        alert=alert_result,
    )