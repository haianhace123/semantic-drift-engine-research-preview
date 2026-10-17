from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import delete

from src.core.database import SessionLocal
from src.models.alert import Alert
from src.models.organization import Organization
from src.models.pipeline_checkpoint import (
    PIPELINE_ALERT_GENERATION_V1,
    PIPELINE_ANOMALY_DETECTION_V1,
    PIPELINE_PATTERN_DETECTION_V1,
    PipelineCheckpoint,
)
from src.models.risk_assessment import RiskAssessment
from src.services.intelligence_pipeline_service import (
    execute_intelligence_pipeline,
)
from src.services.pipeline_source_discovery_service import (
    discover_pipeline_source,
)
from src.services.risk_assessment_service import (
    RISK_VERSION_V2,
)


UTC = timezone.utc


def _make_organization(db):
    suffix = uuid.uuid4().hex[:10]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"intelligence-pipeline-{suffix}",
        slug=f"intelligence-pipeline-{suffix}",
        billing_email=f"{suffix}@example.com",
    )

    db.add(
        organization
    )
    db.flush()

    return organization


def _make_risk_assessment(
    db,
    *,
    organization_id,
    assessed_at,
    created_at,
    score=0.90,
):
    assessment = RiskAssessment(
        id=uuid.uuid4(),
        organization_id=organization_id,
        semantic_drift_event_id=None,
        risk_version=RISK_VERSION_V2,
        assessed_at=assessed_at,
        score=score,
        semantic_magnitude_contribution=0.10,
        legacy_impact_contribution=0.10,
        semantic_severity_contribution=0.10,
        page_context_contribution=0.10,
        confidence_contribution=0.10,
        topology_score=0.10,
        topology_contribution=0.10,
        topology_shift_component=0.10,
        topology_centrality_component=0.10,
        topology_downstream_component=0.10,
        has_topology_signal=True,
        has_previous_topology_state=True,
        created_at=created_at,
    )

    db.add(
        assessment
    )
    db.flush()

    return assessment


def _checkpoint(
    db,
    *,
    organization_id,
    pipeline_name,
):
    return (
        db.query(
            PipelineCheckpoint
        )
        .filter(
            PipelineCheckpoint.organization_id
            == organization_id,
            PipelineCheckpoint.pipeline_name
            == pipeline_name,
        )
        .one_or_none()
    )


def _alerts(
    db,
    *,
    organization_id,
):
    return (
        db.query(
            Alert
        )
        .filter(
            Alert.organization_id
            == organization_id
        )
        .order_by(
            Alert.created_at.asc(),
            Alert.id.asc(),
        )
        .all()
    )


def _cleanup(
    db,
    organization_id,
):
    if organization_id is None:
        return

    db.rollback()

    db.execute(
        delete(
            Alert
        ).where(
            Alert.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            PipelineCheckpoint
        ).where(
            PipelineCheckpoint.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            RiskAssessment
        ).where(
            RiskAssessment.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            Organization
        ).where(
            Organization.id
            == organization_id
        )
    )

    db.commit()


def test_intelligence_pipeline_executes_real_stages_and_checkpoints():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            11,
            0,
            0,
            tzinfo=UTC,
        )

        risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=(
                base
                + timedelta(hours=1)
            ),
            score=0.90,
        )

        db.commit()

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
        )

        assert source is not None
        assert source.source_record_id == risk.id
        assert source.temporal_cursor == risk.assessed_at

        result = execute_intelligence_pipeline(
            db,
            source=source,
        )

        db.commit()

        assert result.organization_id == organization.id
        assert result.source_record_id == risk.id

        assert result.pattern.detected_count == 0
        assert result.pattern.created_count == 0
        assert result.pattern.existing_count == 0

        assert result.anomaly.detected_count == 0
        assert result.anomaly.created_count == 0
        assert result.anomaly.existing_count == 0

        assert result.alert.risk_source_count == 1
        assert result.alert.risk_candidate_count == 1
        assert result.alert.alert_created_count == 1
        assert result.alert.alert_existing_count == 0

        pattern_checkpoint = _checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
        )

        alert_checkpoint = _checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_ALERT_GENERATION_V1
            ),
        )

        anomaly_checkpoint = _checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_ANOMALY_DETECTION_V1
            ),
        )

        assert pattern_checkpoint is not None
        assert (
            pattern_checkpoint.source_created_at
            == risk.created_at
        )
        assert (
            pattern_checkpoint.source_record_id
            == risk.id
        )

        assert alert_checkpoint is not None
        assert (
            alert_checkpoint.source_created_at
            == risk.created_at
        )
        assert (
            alert_checkpoint.source_record_id
            == risk.id
        )

        assert anomaly_checkpoint is None

        alerts = _alerts(
            db,
            organization_id=organization.id,
        )

        assert len(alerts) == 1
        assert alerts[0].source_id == risk.id

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_intelligence_pipeline_mutations_roll_back_together():
    setup_db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            setup_db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            11,
            2,
            0,
            tzinfo=UTC,
        )

        risk = _make_risk_assessment(
            setup_db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=(
                base
                + timedelta(hours=1)
            ),
            score=0.90,
        )

        setup_db.commit()

        execution_db = SessionLocal()

        try:
            source = discover_pipeline_source(
                execution_db,
                organization_id=organization.id,
                pipeline_name=(
                    PIPELINE_PATTERN_DETECTION_V1
                ),
            )

            assert source is not None
            assert source.source_record_id == risk.id

            result = execute_intelligence_pipeline(
                execution_db,
                source=source,
            )

            assert (
                result.pattern_checkpoint.advanced
                is True
            )
            assert (
                result.alert.checkpoint.advanced
                is True
            )

            assert (
                _checkpoint(
                    execution_db,
                    organization_id=organization.id,
                    pipeline_name=(
                        PIPELINE_PATTERN_DETECTION_V1
                    ),
                )
                is not None
            )

            assert (
                _checkpoint(
                    execution_db,
                    organization_id=organization.id,
                    pipeline_name=(
                        PIPELINE_ALERT_GENERATION_V1
                    ),
                )
                is not None
            )

            assert len(
                _alerts(
                    execution_db,
                    organization_id=organization.id,
                )
            ) == 1

            execution_db.rollback()

        finally:
            execution_db.close()

        verify_db = SessionLocal()

        try:
            assert (
                _checkpoint(
                    verify_db,
                    organization_id=organization.id,
                    pipeline_name=(
                        PIPELINE_PATTERN_DETECTION_V1
                    ),
                )
                is None
            )

            assert (
                _checkpoint(
                    verify_db,
                    organization_id=organization.id,
                    pipeline_name=(
                        PIPELINE_ALERT_GENERATION_V1
                    ),
                )
                is None
            )

            assert (
                _checkpoint(
                    verify_db,
                    organization_id=organization.id,
                    pipeline_name=(
                        PIPELINE_ANOMALY_DETECTION_V1
                    ),
                )
                is None
            )

            assert (
                _alerts(
                    verify_db,
                    organization_id=organization.id,
                )
                == []
            )

        finally:
            verify_db.close()

    finally:
        try:
            _cleanup(
                setup_db,
                organization_id,
            )
        finally:
            setup_db.close()