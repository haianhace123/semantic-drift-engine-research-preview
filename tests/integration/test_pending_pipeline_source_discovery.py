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
from src.models.organization_work_schedule import (
    WORK_KIND_ALERT_RECONCILIATION,
    WORK_KIND_INTELLIGENCE,
    OrganizationWorkSchedule,
)
from src.models.pipeline_checkpoint import (
    PipelineCheckpoint,
    PIPELINE_ANOMALY_DETECTION_V1,
    PIPELINE_PATTERN_DETECTION_V1,
)
from src.models.risk_assessment import RiskAssessment
from src.services.organization_work_schedule_service import (
    schedule_organization_work_retry,
)
from src.services.pipeline_checkpoint_service import (
    persist_pipeline_checkpoint,
)
from src.services.pipeline_source_discovery_service import (
    discover_pending_pipeline_sources,
)
from src.services.risk_assessment_service import (
    RISK_VERSION_V2,
)


def _make_organization(
    db,
    *,
    suffix=None,
):
    suffix = (
        suffix
        or uuid.uuid4().hex[:10]
    )

    organization = Organization(
        id=uuid.uuid4(),
        name=f"pending-source-{suffix}",
        slug=f"pending-source-{suffix}",
        billing_email=(
            f"{suffix}@example.com"
        ),
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

    db.add(
        assessment
    )
    db.flush()

    return assessment


def _cleanup(
    db,
    organization_ids,
):
    db.rollback()

    if not organization_ids:
        return

    db.execute(
        delete(
            OrganizationWorkSchedule
        ).where(
            OrganizationWorkSchedule.organization_id.in_(
                organization_ids
            )
        )
    )


    db.execute(
        delete(
            PipelineCheckpoint
        ).where(
            PipelineCheckpoint.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(
            RiskAssessment
        ).where(
            RiskAssessment.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(
            Organization
        ).where(
            Organization.id.in_(
                organization_ids
            )
        )
    )

    db.commit()

def _only_organizations(
    results,
    organization_ids,
):
    organization_id_set = set(
        organization_ids
    )

    return [
        result
        for result in results
        if result.organization_id
        in organization_id_set
    ]

def test_discovers_pending_organizations_and_excludes_up_to_date():
    db = SessionLocal()
    organization_ids = []

    try:
        base = datetime(
            2026,
            9,
            1,
            0,
            0,
            tzinfo=timezone.utc,
        )

        pending_old = _make_organization(
            db
        )
        pending_new = _make_organization(
            db
        )
        completed = _make_organization(
            db
        )

        organization_ids.extend(
            [
                pending_old.id,
                pending_new.id,
                completed.id,
            ]
        )

        old_source = _make_risk_assessment(
            db,
            organization_id=pending_old.id,
            assessed_at=base
            + timedelta(days=1),
            created_at=base
            + timedelta(days=10),
        )

        new_source = _make_risk_assessment(
            db,
            organization_id=pending_new.id,
            assessed_at=base
            + timedelta(days=2),
            created_at=base
            + timedelta(days=12),
        )

        completed_source = (
            _make_risk_assessment(
                db,
                organization_id=completed.id,
                assessed_at=base
                + timedelta(days=3),
                created_at=base
                + timedelta(days=11),
            )
        )

        db.commit()

        persist_pipeline_checkpoint(
            db,
            organization_id=completed.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
            source_created_at=(
                completed_source.created_at
            ),
            source_record_id=(
                completed_source.id
            ),
            knowledge_cursor=(
                completed_source.assessed_at
            ),
        )

        db.commit()

        results = (
            discover_pending_pipeline_sources(
                db,
                pipeline_name=(
                    PIPELINE_PATTERN_DETECTION_V1
                ),
                limit=100,
            )
        )

        results = _only_organizations(
            results,
            organization_ids,
        )

        assert [
            result.organization_id
            for result in results
        ] == [
            pending_old.id,
            pending_new.id,
        ]

        assert (
            results[0].source_record_id
            == old_source.id
        )
        assert (
            results[1].source_record_id
            == new_source.id
        )

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_latest_materialization_is_used_once_per_organization():
    db = SessionLocal()
    organization_ids = []

    try:
        organization = (
            _make_organization(
                db
            )
        )
        organization_ids.append(
            organization.id
        )

        base = datetime(
            2026,
            9,
            1,
            0,
            0,
            tzinfo=timezone.utc,
        )

        _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base
            + timedelta(days=1),
            created_at=base
            + timedelta(days=5),
        )

        latest = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base
            + timedelta(days=3),
            created_at=base
            + timedelta(days=7),
        )

        db.commit()

        results = (
            discover_pending_pipeline_sources(
                db,
                pipeline_name=(
                    PIPELINE_PATTERN_DETECTION_V1
                ),
                limit=100,
            )
        )

        results = _only_organizations(
            results,
            organization_ids,
        )

        assert len(results) == 1

        assert (
            results[0].organization_id
            == organization.id
        )
        assert (
            results[0].source_created_at
            == latest.created_at
        )
        assert (
            results[0].source_record_id
            == latest.id
        )

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_pattern_and_anomaly_pending_sets_are_independent():
    db = SessionLocal()
    organization_ids = []

    try:
        organization = (
            _make_organization(
                db
            )
        )
        organization_ids.append(
            organization.id
        )

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
        )

        db.commit()

        pattern_results = (
            discover_pending_pipeline_sources(
                db,
                pipeline_name=(
                    PIPELINE_PATTERN_DETECTION_V1
                ),
                limit=100,
            )
        )

        anomaly_results = (
            discover_pending_pipeline_sources(
                db,
                pipeline_name=(
                    PIPELINE_ANOMALY_DETECTION_V1
                ),
                limit=100,
            )
        )

        pattern_results = (
            _only_organizations(
                pattern_results,
                organization_ids,
            )
        )

        anomaly_results = (
            _only_organizations(
                anomaly_results,
                organization_ids,
            )
        )

        assert pattern_results == []

        assert len(
            anomaly_results
        ) == 1

        assert (
            anomaly_results[0].organization_id
            == organization.id
        )

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_limit_applies_after_up_to_date_organizations_are_filtered():
    db = SessionLocal()
    organization_ids = []

    try:
        base = datetime(
            2026,
            9,
            1,
            0,
            0,
            tzinfo=timezone.utc,
        )

        completed = _make_organization(
            db
        )
        pending_first = _make_organization(
            db
        )
        pending_second = _make_organization(
            db
        )

        organization_ids.extend(
            [
                completed.id,
                pending_first.id,
                pending_second.id,
            ]
        )

        completed_source = (
            _make_risk_assessment(
                db,
                organization_id=completed.id,
                assessed_at=base,
                created_at=base
                + timedelta(days=1),
            )
        )

        first_source = (
            _make_risk_assessment(
                db,
                organization_id=(
                    pending_first.id
                ),
                assessed_at=base
                + timedelta(days=1),
                created_at=base
                + timedelta(days=2),
            )
        )

        _make_risk_assessment(
            db,
            organization_id=(
                pending_second.id
            ),
            assessed_at=base
            + timedelta(days=2),
            created_at=base
            + timedelta(days=3),
        )

        db.commit()

        persist_pipeline_checkpoint(
            db,
            organization_id=completed.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
            source_created_at=(
                completed_source.created_at
            ),
            source_record_id=(
                completed_source.id
            ),
            knowledge_cursor=(
                completed_source.assessed_at
            ),
        )

        db.commit()

        results = (
            discover_pending_pipeline_sources(
                db,
                pipeline_name=(
                    PIPELINE_PATTERN_DETECTION_V1
                ),
                limit=1,
            )
        )

        assert len(results) == 1
        assert (
            results[0].organization_id
            == pending_first.id
        )
        assert (
            results[0].source_record_id
            == first_source.id
        )

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_non_v2_rows_do_not_enter_pending_set():
    db = SessionLocal()
    organization_ids = []

    try:
        organization = (
            _make_organization(
                db
            )
        )
        organization_ids.append(
            organization.id
        )

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
                2,
                0,
                tzinfo=timezone.utc,
            ),
            risk_version="deterministic_v1",
        )

        db.commit()

        results = (
            discover_pending_pipeline_sources(
                db,
                pipeline_name=(
                    PIPELINE_PATTERN_DETECTION_V1
                ),
                limit=100,
            )
        )

        results = _only_organizations(
            results,
            organization_ids,
        )

        assert results == []

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_intelligence_schedule_controls_only_intelligence_eligibility():
    db = SessionLocal()
    organization_ids = []

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        organization_ids.append(
            organization_id
        )

        now = datetime.now(
            timezone.utc
        )

        source = _make_risk_assessment(
            db,
            organization_id=organization_id,
            assessed_at=now - timedelta(hours=2),
            created_at=now - timedelta(hours=1),
        )

        source_id = source.id

        db.commit()

        schedule_organization_work_retry(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_ALERT_RECONCILIATION,
            retry_seconds=3600,
            now=now,
        )

        db.commit()

        results = discover_pending_pipeline_sources(
            db,
            pipeline_name=PIPELINE_PATTERN_DETECTION_V1,
            limit=100,
        )

        results = _only_organizations(
            results,
            organization_ids,
        )

        assert [
            result.organization_id
            for result in results
        ] == [
            organization.id
        ]

        assert (
            results[0].source_record_id
            == source.id
        )

        schedule_organization_work_retry(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
            retry_seconds=3600,
            now=now,
        )

        db.commit()

        results = discover_pending_pipeline_sources(
            db,
            pipeline_name=PIPELINE_PATTERN_DETECTION_V1,
            limit=100,
        )

        assert _only_organizations(
            results,
            organization_ids,
        ) == []

        schedule = (
            db.query(
                OrganizationWorkSchedule
            )
            .filter(
                OrganizationWorkSchedule.organization_id
                == organization.id,
                OrganizationWorkSchedule.work_kind
                == WORK_KIND_INTELLIGENCE,
            )
            .one()
        )

        schedule.retry_after = (
            now - timedelta(seconds=1)
        )

        db.commit()
        db.close()

        db = SessionLocal()

        results = discover_pending_pipeline_sources(
            db,
            pipeline_name=PIPELINE_PATTERN_DETECTION_V1,
            limit=100,
        )

        results = _only_organizations(
            results,
            organization_ids,
        )

        assert [
            result.organization_id
            for result in results
        ] == [
            organization_id
        ]

        assert (
            results[0].source_record_id
            == source_id
        )

    finally:
        db.close()

        cleanup_db = SessionLocal()

        try:
            _cleanup(
                cleanup_db,
                organization_ids,
            )
        finally:
            cleanup_db.close()
