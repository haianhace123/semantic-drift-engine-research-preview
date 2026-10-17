from __future__ import annotations

import uuid
from datetime import (
    datetime,
    timedelta,
    timezone,
)

from sqlalchemy import delete

from src.core.database import SessionLocal
from src.models.organization import Organization
from src.models.pipeline_checkpoint import (
    PipelineCheckpoint,
    PIPELINE_ANOMALY_DETECTION_V1,
    PIPELINE_PATTERN_DETECTION_V1,
)
from src.models.risk_assessment import RiskAssessment
from src.services.pipeline_checkpoint_service import (
    persist_pipeline_checkpoint,
)
from src.services.pipeline_source_discovery_service import (
    discover_pipeline_source,
)
from src.services.risk_assessment_service import (
    RISK_VERSION_V2,
)


def _make_organization(db):
    suffix = uuid.uuid4().hex[:10]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"pipeline-source-{suffix}",
        slug=f"pipeline-source-{suffix}",
        billing_email=f"{suffix}@example.com",
    )

    db.add(organization)
    db.flush()

    return organization


def _make_risk_assessment(
    db,
    *,
    organization_id,
    assessed_at,
    created_at,
    assessment_id=None,
    risk_version=RISK_VERSION_V2,
):
    assessment = RiskAssessment(
        id=assessment_id or uuid.uuid4(),
        organization_id=organization_id,
        semantic_drift_event_id=None,
        risk_version=risk_version,
        assessed_at=assessed_at,
        score=0.50,
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

    db.add(assessment)
    db.flush()

    return assessment


def _cleanup(
    db,
    organization_id,
):
    if organization_id is None:
        return

    db.rollback()

    db.execute(
        delete(PipelineCheckpoint).where(
            PipelineCheckpoint.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(RiskAssessment).where(
            RiskAssessment.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(Organization).where(
            Organization.id
            == organization_id
        )
    )

    db.commit()


def test_first_discovery_uses_latest_source_and_max_temporal_cursor():
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
            1,
            0,
            0,
            tzinfo=timezone.utc,
        )

        first = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base
            + timedelta(days=5),
            created_at=base
            + timedelta(days=10),
        )

        latest = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base
            + timedelta(days=3),
            created_at=base
            + timedelta(days=11),
        )

        db.commit()

        result = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
        )

        assert result is not None
        assert (
            result.organization_id
            == organization.id
        )
        assert (
            result.pipeline_name
            == PIPELINE_PATTERN_DETECTION_V1
        )
        assert (
            result.source_created_at
            == latest.created_at
        )
        assert (
            result.source_record_id
            == latest.id
        )

        # Effective time comes from the greatest assessed_at,
        # not necessarily from the latest materialized row.
        assert (
            result.temporal_cursor
            == first.assessed_at
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_up_to_date_checkpoint_returns_none():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        assessed_at = datetime(
            2026,
            9,
            5,
            12,
            0,
            tzinfo=timezone.utc,
        )

        source = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=assessed_at,
            created_at=datetime(
                2026,
                9,
                9,
                1,
                0,
                tzinfo=timezone.utc,
            ),
        )

        db.commit()

        persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
            source_created_at=(
                source.created_at
            ),
            source_record_id=source.id,
            knowledge_cursor=assessed_at,
            metadata_json={
                "test": "up-to-date",
            },
        )

        db.commit()

        result = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
        )

        assert result is None

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_newer_risk_materialization_becomes_pending():
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
            1,
            0,
            0,
            tzinfo=timezone.utc,
        )

        first = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base
            + timedelta(days=2),
            created_at=base
            + timedelta(days=5),
        )

        db.commit()

        persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
            source_created_at=(
                first.created_at
            ),
            source_record_id=first.id,
            knowledge_cursor=(
                first.assessed_at
            ),
        )

        db.commit()

        latest = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base
            + timedelta(days=6),
            created_at=base
            + timedelta(days=8),
        )

        db.commit()

        result = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
        )

        assert result is not None
        assert (
            result.source_created_at
            == latest.created_at
        )
        assert (
            result.source_record_id
            == latest.id
        )
        assert (
            result.temporal_cursor
            == latest.assessed_at
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_backfill_does_not_move_temporal_cursor_backward():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            8,
            1,
            0,
            0,
            tzinfo=timezone.utc,
        )

        original = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base
            + timedelta(days=20),
            created_at=base
            + timedelta(days=21),
        )

        db.commit()

        persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
            source_created_at=(
                original.created_at
            ),
            source_record_id=original.id,
            knowledge_cursor=(
                original.assessed_at
            ),
        )

        db.commit()

        # Materialized later, but belongs to much older
        # effective/event time.
        backfill = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base
            + timedelta(days=3),
            created_at=base
            + timedelta(days=30),
        )

        db.commit()

        result = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
        )

        assert result is not None

        assert (
            result.source_created_at
            == backfill.created_at
        )
        assert (
            result.source_record_id
            == backfill.id
        )

        # The new materialization triggers work, but the temporal
        # detector cursor must not regress.
        assert (
            result.temporal_cursor
            == original.assessed_at
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_pattern_and_anomaly_discovery_use_independent_checkpoints():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        assessed_at = datetime(
            2026,
            9,
            8,
            0,
            0,
            tzinfo=timezone.utc,
        )

        source = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=assessed_at,
            created_at=datetime(
                2026,
                9,
                9,
                2,
                0,
                tzinfo=timezone.utc,
            ),
        )

        db.commit()

        persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
            source_created_at=(
                source.created_at
            ),
            source_record_id=source.id,
            knowledge_cursor=assessed_at,
        )

        db.commit()

        pattern_result = (
            discover_pipeline_source(
                db,
                organization_id=organization.id,
                pipeline_name=(
                    PIPELINE_PATTERN_DETECTION_V1
                ),
            )
        )

        anomaly_result = (
            discover_pipeline_source(
                db,
                organization_id=organization.id,
                pipeline_name=(
                    PIPELINE_ANOMALY_DETECTION_V1
                ),
            )
        )

        assert pattern_result is None

        assert anomaly_result is not None
        assert (
            anomaly_result.source_created_at
            == source.created_at
        )
        assert (
            anomaly_result.source_record_id
            == source.id
        )
        assert (
            anomaly_result.temporal_cursor
            == assessed_at
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_same_created_at_uses_uuid_tiebreaker_deterministically():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        created_at = datetime(
            2026,
            9,
            9,
            3,
            0,
            tzinfo=timezone.utc,
        )

        low_id = uuid.UUID(
            "00000000-0000-0000-0000-000000000001"
        )
        high_id = uuid.UUID(
            "ffffffff-ffff-ffff-ffff-ffffffffffff"
        )

        low = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessment_id=low_id,
            assessed_at=datetime(
                2026,
                9,
                7,
                0,
                0,
                tzinfo=timezone.utc,
            ),
            created_at=created_at,
        )

        high = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessment_id=high_id,
            assessed_at=datetime(
                2026,
                9,
                8,
                0,
                0,
                tzinfo=timezone.utc,
            ),
            created_at=created_at,
        )

        db.commit()

        result = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
        )

        assert result is not None
        assert (
            result.source_created_at
            == created_at
        )
        assert (
            result.source_record_id
            == high.id
        )
        assert (
            result.source_record_id
            != low.id
        )
        assert (
            result.temporal_cursor
            == high.assessed_at
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_non_v2_assessment_does_not_trigger_pipeline():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=datetime(
                2026,
                9,
                8,
                0,
                0,
                tzinfo=timezone.utc,
            ),
            created_at=datetime(
                2026,
                9,
                9,
                4,
                0,
                tzinfo=timezone.utc,
            ),
            risk_version="deterministic_v1",
        )

        db.commit()

        result = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
        )

        assert result is None

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()