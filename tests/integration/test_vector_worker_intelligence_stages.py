from __future__ import annotations

import uuid
from datetime import (
    datetime,
    timedelta,
    timezone,
)

import pytest
from sqlalchemy import delete

from scripts import vector_worker as worker_module
from scripts.vector_worker import (
    BatchStats,
    VectorWorker,
)
from src.core.database import SessionLocal
from src.models.organization import Organization
from src.models.pipeline_checkpoint import (
    PipelineCheckpoint,
    PIPELINE_PATTERN_DETECTION_V1,
)
from src.models.risk_assessment import RiskAssessment
from src.services.risk_assessment_service import (
    RISK_VERSION_V2,
)


pytestmark = pytest.mark.skipif(
    not bool(
        __import__("os").getenv(
            "RUN_DB_INTEGRATION_TESTS"
        )
    ),
    reason=(
        "Set RUN_DB_INTEGRATION_TESTS=1 "
        "to run database integration tests."
    ),
)

def _make_organization(
    db,
):
    suffix = uuid.uuid4().hex[:10]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"worker-intelligence-{suffix}",
        slug=f"worker-intelligence-{suffix}",
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
):
    assessment = RiskAssessment(
        id=uuid.uuid4(),
        organization_id=organization_id,
        semantic_drift_event_id=None,
        risk_version=RISK_VERSION_V2,
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
    organization_ids,
):
    db.rollback()

    if not organization_ids:
        return

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


def _checkpoint(
    db,
    *,
    organization_id,
    pipeline_name,
):
    db.expire_all()

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


def _worker(
    *,
    pattern_enabled=True,
    anomaly_enabled=True,
):
    return VectorWorker(
        snapshot_limit=1,
        event_limit=1,
        risk_limit=1,
        pattern_limit=100,
        anomaly_limit=100,
        max_attempts=3,
        process_embeddings=False,
        process_analysis=False,
        process_risk_scoring=False,
        process_pattern_detection=(
            pattern_enabled
        ),
        process_anomaly_detection=(
            anomaly_enabled
        ),
    )


def test_intelligence_zero_result_advances_transactional_checkpoint(
    monkeypatch,
):
    db = SessionLocal()
    organization_ids = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        assessed_at = datetime(
            2026,
            9,
            5,
            12,
            0,
            tzinfo=timezone.utc,
        )
        created_at = assessed_at + timedelta(
            days=3
        )

        source = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=assessed_at,
            created_at=created_at,
        )

        db.commit()

        calls = []

        real_execute_intelligence_pipeline = (
            worker_module.execute_intelligence_pipeline
        )

        def execute_and_record(
            execution_db,
            *,
            source,
        ):
            calls.append(
                {
                    "organization_id": (
                        source.organization_id
                    ),
                    "pipeline_name": (
                        source.pipeline_name
                    ),
                    "source_created_at": (
                        source.source_created_at
                    ),
                    "source_record_id": (
                        source.source_record_id
                    ),
                    "temporal_cursor": (
                        source.temporal_cursor
                    ),
                }
            )

            return real_execute_intelligence_pipeline(
                execution_db,
                source=source,
            )

        monkeypatch.setattr(
            worker_module,
            "execute_intelligence_pipeline",
            execute_and_record,
        )

        monkeypatch.setattr(
            worker_module,
            "discover_pending_pipeline_sources",
            lambda discovery_db, *, pipeline_name, limit: [
                worker_module.discover_pipeline_source(
                    discovery_db,
                    organization_id=organization.id,
                    pipeline_name=pipeline_name,
                )
            ],
        )

        worker = _worker()

        stats = (
            worker.process_intelligence_organizations()
        )

        assert stats.selected == 1
        assert stats.completed == 1
        assert stats.failed == 0
        assert stats.deferred == 0

        assert calls == [
            {
                "organization_id": (
                    organization.id
                ),
                "pipeline_name": (
                    PIPELINE_PATTERN_DETECTION_V1
                ),
                "source_created_at": (
                    created_at
                ),
                "source_record_id": (
                    source.id
                ),
                "temporal_cursor": (
                    assessed_at
                ),
            }
        ]

        checkpoint = _checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
        )

        assert checkpoint is not None
        assert (
            checkpoint.source_created_at
            == created_at
        )
        assert (
            checkpoint.source_record_id
            == source.id
        )
        assert (
            checkpoint.knowledge_cursor
            == assessed_at
        )
        assert checkpoint.metadata_json == {
            "detected_count": 0,
            "created_count": 0,
            "existing_count": 0,
        }

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_intelligence_exception_rolls_back_without_checkpoint(
    monkeypatch,
):
    db = SessionLocal()
    organization_ids = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        assessed_at = datetime(
            2026,
            9,
            6,
            12,
            0,
            tzinfo=timezone.utc,
        )

        _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=assessed_at,
            created_at=(
                assessed_at
                + timedelta(days=2)
            ),
        )

        db.commit()

        monkeypatch.setattr(
            worker_module,
            "discover_pending_pipeline_sources",
            lambda discovery_db, *, pipeline_name, limit: [
                worker_module.discover_pipeline_source(
                    discovery_db,
                    organization_id=organization.id,
                    pipeline_name=pipeline_name,
                )
            ],
        )

        def fail_intelligence_pipeline(
            execution_db,
            *,
            source,
        ):
            checkpoint = PipelineCheckpoint(
                id=uuid.uuid4(),
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
                    "forced": True,
                },
            )

            execution_db.add(
                checkpoint
            )
            execution_db.flush()

            raise RuntimeError(
                "forced intelligence failure"
            )

        monkeypatch.setattr(
            worker_module,
            "execute_intelligence_pipeline",
            fail_intelligence_pipeline,
        )

        worker = _worker()

        stats = (
            worker.process_intelligence_organizations()
        )

        assert stats.selected == 1
        assert stats.completed == 0
        assert stats.failed == 1
        assert stats.deferred == 0

        checkpoint = _checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
        )

        assert checkpoint is None

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_failed_intelligence_organization_does_not_starve_later_ready_organization(
    monkeypatch,
):
    db = SessionLocal()
    organization_ids = []

    try:
        poison_organization = _make_organization(
            db
        )
        healthy_organization = _make_organization(
            db
        )
        organization_ids.extend(
            [
                poison_organization.id,
                healthy_organization.id,
            ]
        )

        poison_assessed_at = datetime(
            2036,
            9,
            6,
            12,
            0,
            tzinfo=timezone.utc,
        )
        healthy_assessed_at = (
            poison_assessed_at
            + timedelta(days=1)
        )

        _make_risk_assessment(
            db,
            organization_id=poison_organization.id,
            assessed_at=poison_assessed_at,
            created_at=poison_assessed_at,
        )
        _make_risk_assessment(
            db,
            organization_id=healthy_organization.id,
            assessed_at=healthy_assessed_at,
            created_at=healthy_assessed_at,
        )

        db.commit()

        real_execute_intelligence_pipeline = (
            worker_module.execute_intelligence_pipeline
        )

        def fail_poison_organization(
            execution_db,
            *,
            source,
        ):
            if (
                source.organization_id
                == poison_organization.id
            ):
                raise RuntimeError(
                    "forced intelligence failure"
                )

            return real_execute_intelligence_pipeline(
                execution_db,
                source=source,
            )

        monkeypatch.setattr(
            worker_module,
            "execute_intelligence_pipeline",
            fail_poison_organization,
        )

        worker = _worker()
        worker.pattern_limit = 1
        worker.anomaly_limit = 1

        first_stats = (
            worker.process_intelligence_organizations()
        )

        assert first_stats.selected == 1
        assert first_stats.completed == 0
        assert first_stats.failed == 1
        assert first_stats.deferred == 0

        assert (
            _checkpoint(
                db,
                organization_id=poison_organization.id,
                pipeline_name=(
                    PIPELINE_PATTERN_DETECTION_V1
                ),
            )
            is None
        )

        second_stats = (
            worker.process_intelligence_organizations()
        )

        assert second_stats.selected == 1
        assert second_stats.completed == 1
        assert second_stats.failed == 0
        assert second_stats.deferred == 0

        healthy_checkpoint = _checkpoint(
            db,
            organization_id=healthy_organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
        )

        assert healthy_checkpoint is not None

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_intelligence_pipeline_uses_one_shared_risk_watermark(
    monkeypatch,
):
    db = SessionLocal()
    organization_ids = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        assessed_at = datetime(
            2026,
            9,
            7,
            12,
            0,
            tzinfo=timezone.utc,
        )
        created_at = assessed_at + timedelta(
            days=1
        )

        source = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=assessed_at,
            created_at=created_at,
        )

        db.commit()

        captured_sources = []

        real_execute_intelligence_pipeline = (
            worker_module.execute_intelligence_pipeline
        )

        def execute_and_capture_source(
            execution_db,
            *,
            source,
        ):
            captured_sources.append(
                source
            )

            return real_execute_intelligence_pipeline(
                execution_db,
                source=source,
            )

        monkeypatch.setattr(
            worker_module,
            "execute_intelligence_pipeline",
            execute_and_capture_source,
        )

        monkeypatch.setattr(
            worker_module,
            "discover_pending_pipeline_sources",
            lambda discovery_db, *, pipeline_name, limit: [
                worker_module.discover_pipeline_source(
                    discovery_db,
                    organization_id=organization.id,
                    pipeline_name=pipeline_name,
                )
            ],
        )

        worker = _worker()

        stats = (
            worker.process_intelligence_organizations()
        )

        assert stats.selected == 1
        assert stats.completed == 1
        assert stats.failed == 0
        assert stats.deferred == 0

        assert len(
            captured_sources
        ) == 1

        discovered_source = (
            captured_sources[0]
        )

        assert (
            discovered_source.organization_id
            == organization.id
        )
        assert (
            discovered_source.pipeline_name
            == PIPELINE_PATTERN_DETECTION_V1
        )
        assert (
            discovered_source.source_created_at
            == created_at
        )
        assert (
            discovered_source.source_record_id
            == source.id
        )
        assert (
            discovered_source.temporal_cursor
            == assessed_at
        )

        checkpoint = _checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_PATTERN_DETECTION_V1
            ),
        )

        assert checkpoint is not None
        assert (
            checkpoint.source_created_at
            == discovered_source.source_created_at
        )
        assert (
            checkpoint.source_record_id
            == discovered_source.source_record_id
        )
        assert (
            checkpoint.knowledge_cursor
            == discovered_source.temporal_cursor
        )

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_stale_intelligence_candidate_is_deferred_without_execution(
    monkeypatch,
):
    organization_id = uuid.uuid4()

    candidate = type(
        "Candidate",
        (),
        {
            "organization_id": (
                organization_id
            )
        },
    )()

    monkeypatch.setattr(
        worker_module,
        "discover_pending_pipeline_sources",
        lambda discovery_db, *, pipeline_name, limit: [
            candidate
        ],
    )

    monkeypatch.setattr(
        worker_module,
        "discover_pipeline_source",
        lambda processing_db, **kwargs: None,
    )

    executed = []

    def unexpected_execution(
        execution_db,
        *,
        source,
    ):
        executed.append(
            source
        )

        raise AssertionError(
            "Stale intelligence source must not execute."
        )

    monkeypatch.setattr(
        worker_module,
        "execute_intelligence_pipeline",
        unexpected_execution,
    )

    worker = _worker()

    stats = (
        worker.process_intelligence_organizations()
    )

    assert stats.selected == 1
    assert stats.completed == 0
    assert stats.failed == 0
    assert stats.deferred == 1
    assert executed == []


def test_run_cycle_keeps_risk_before_transactional_intelligence(
    monkeypatch,
):
    worker = _worker()

    order = []

    def risk_stage():
        order.append(
            "risk"
        )
        return BatchStats(
            selected=1,
            completed=1,
        )

    intelligence_stats = BatchStats(
        selected=1,
        completed=1,
    )

    def intelligence_stage():
        order.append(
            "intelligence"
        )
        return intelligence_stats

    monkeypatch.setattr(
        worker,
        "process_risk_events",
        risk_stage,
    )
    monkeypatch.setattr(
        worker,
        "process_intelligence_organizations",
        intelligence_stage,
    )

    worker.process_risk_scoring = True

    stats = worker.run_cycle()

    assert order == [
        "risk",
        "intelligence",
    ]

    assert (
        stats.risk_events.completed
        == 1
    )
    assert (
        stats.pattern_organizations
        is intelligence_stats
    )
    assert (
        stats.anomaly_organizations
        is intelligence_stats
    )