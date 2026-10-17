from __future__ import annotations

import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

import scripts.vector_worker as vector_worker_module
from scripts.vector_worker import VectorWorker
from src.core.database import SessionLocal
from src.models.page_snapshot import (
    EMBEDDING_DIMENSIONS,
    PageSnapshot,
)
from src.models.semantic_drift_event import (
    TEMPORAL_INVALIDATION_STATUS_COMPLETED,
    TEMPORAL_INVALIDATION_STATUS_FAILED,
    TEMPORAL_INVALIDATION_STATUS_PENDING,
    VECTOR_ANALYSIS_STATUS_COMPLETED,
    VECTOR_ANALYSIS_STATUS_PENDING,
    SemanticDriftEvent,
)
from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.models.lineage_invalidation_decision import (
    LineageInvalidationDecisionRecord,
)
from src.models.lineage_invalidation_execution_intent import (
    LineageInvalidationExecutionIntentRecord,
)
from src.models.lineage_observation import (
    LineageObservationRecord,
)
from src.models.risk_assessment import RiskAssessment
from src.models.temporal_lineage_correlation import (
    TemporalLineageCorrelationRecord,
)
from src.models.temporal_lineage_invalidation_decision_provenance import (
    TemporalLineageInvalidationDecisionProvenanceRecord,
)
from src.services.lineage_observation_persistence_service import (
    persist_lineage_observation,
)
from src.services.temporal_lineage_correlation_service import (
    persist_temporal_lineage_correlation,
)
from src.services.embedding_service import (
    EmbeddingProviderUnavailableError,
    SnapshotContentError,
)
from src.services.risk_version import (
    RISK_VERSION_V2,
    RISK_VERSION_V3,
)
from src.services.risk_scoring_v3_shadow_orchestrator import (
    RiskScoringV3ShadowNotReadyError,
)


RUN_DB_INTEGRATION_TESTS = (
    os.getenv(
        "RUN_DB_INTEGRATION_TESTS",
        "",
    ).strip()
    == "1"
)


pytestmark = pytest.mark.skipif(
    not RUN_DB_INTEGRATION_TESTS,
    reason=(
        "Set RUN_DB_INTEGRATION_TESTS=1 "
        "to run database integration tests."
    ),
)


TEST_EMBEDDING_MODEL = (
    "integration-test-vector-worker"
)

class DeterministicEmbeddingService:
    """
    Test-only embedding service that persists deterministic vectors.

    This exercises the real worker orchestration and database lifecycle
    without making any external embedding-provider call.
    """

    def __init__(
        self,
        embeddings_by_snapshot_id: dict[str, list[float]],
    ) -> None:
        self.embeddings_by_snapshot_id = (
            embeddings_by_snapshot_id
        )
        self.called_snapshot_ids: list[str] = []

    def embed_snapshot(
        self,
        db,
        snapshot: PageSnapshot,
    ) -> PageSnapshot:
        snapshot_id = str(snapshot.id)

        self.called_snapshot_ids.append(
            snapshot_id
        )

        embedding = (
            self.embeddings_by_snapshot_id[
                snapshot_id
            ]
        )

        snapshot.embedding = embedding
        snapshot.embedding_model = (
            TEST_EMBEDDING_MODEL
        )
        snapshot.embedding_dimensions = (
            EMBEDDING_DIMENSIONS
        )
        snapshot.embedded_at = datetime.now(
            timezone.utc
        )
        snapshot.embedding_status = "completed"
        snapshot.embedding_attempts = (
            int(
                snapshot.embedding_attempts
                or 0
            )
            + 1
        )
        snapshot.embedding_error = None

        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)

        return snapshot

class ProviderUnavailableEmbeddingService:
    def __init__(self) -> None:
        self.called_snapshot_ids: list[str] = []

    def embed_snapshot(
        self,
        db,
        snapshot: PageSnapshot,
    ) -> PageSnapshot:
        self.called_snapshot_ids.append(
            str(snapshot.id)
        )

        snapshot.embedding_status = "failed"
        snapshot.embedding_attempts = (
            int(
                snapshot.embedding_attempts
                or 0
            )
            + 1
        )
        snapshot.embedding_error = (
            "Embedding provider quota is exhausted."
        )

        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)

        raise EmbeddingProviderUnavailableError(
            "Embedding provider quota is exhausted."
        )


class SnapshotSpecificFailureEmbeddingService:
    def __init__(self) -> None:
        self.called_snapshot_ids: list[str] = []

    def embed_snapshot(
        self,
        db,
        snapshot: PageSnapshot,
    ) -> PageSnapshot:
        self.called_snapshot_ids.append(
            str(snapshot.id)
        )

        if len(
            self.called_snapshot_ids
        ) == 1:
            snapshot.embedding_status = "failed"
            snapshot.embedding_attempts = (
                int(
                    snapshot.embedding_attempts
                    or 0
                )
                + 1
            )
            snapshot.embedding_error = (
                "Snapshot content is invalid."
            )

            db.add(snapshot)
            db.commit()
            db.refresh(snapshot)

            raise SnapshotContentError(
                "Snapshot content is invalid."
            )

        snapshot.embedding = _vector(0)
        snapshot.embedding_model = (
            TEST_EMBEDDING_MODEL
        )
        snapshot.embedding_dimensions = (
            EMBEDDING_DIMENSIONS
        )
        snapshot.embedding_status = "completed"
        snapshot.embedding_attempts = (
            int(
                snapshot.embedding_attempts
                or 0
            )
            + 1
        )
        snapshot.embedding_error = None

        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)

        return snapshot


def _vector(
    index: int,
) -> list[float]:
    """
    Create a deterministic non-zero embedding.
    """
    values = [
        0.0
        for _ in range(
            EMBEDDING_DIMENSIONS
        )
    ]

    values[index] = 1.0

    return values


def _existing_snapshot(
    db,
) -> PageSnapshot:
    """
    Find an existing row that can provide valid
    organization/page ownership for disposable rows.
    """
    existing = (
        db.query(
            PageSnapshot
        )
        .order_by(
            PageSnapshot.crawled_at.desc()
        )
        .first()
    )

    if existing is None:
        pytest.skip(
            "No existing snapshot is available "
            "to provide page/tenant ownership."
        )

    return existing


def _make_snapshot(
    db,
    *,
    existing: PageSnapshot,
    raw_content: str,
    embedding: list[float] | None,
    embedding_status: str,
    crawled_at: datetime | None = None,
) -> PageSnapshot:
    """
    Create one disposable snapshot.
    """
    content_hash = hashlib.sha256(
        (
            raw_content
            + str(
                uuid.uuid4()
            )
        ).encode(
            "utf-8"
        )
    ).hexdigest()

    snapshot = PageSnapshot(
        organization_id=(
            existing.organization_id
        ),
        monitored_page_id=(
            existing.monitored_page_id
        ),
        content_hash=content_hash,
        raw_content=raw_content,
        raw_content_path=None,
        content_size=len(
            raw_content.encode(
                "utf-8"
            )
        ),
        embedding=embedding,
        embedding_model=(
            TEST_EMBEDDING_MODEL
            if embedding is not None
            else None
        ),
        embedding_dimensions=(
            EMBEDDING_DIMENSIONS
            if embedding is not None
            else None
        ),
        embedding_status=(
            embedding_status
        ),
        embedding_attempts=(
            1
            if embedding_status
            == "completed"
            else 0
        ),
    )

    if crawled_at is not None:
        snapshot.crawled_at = (
            crawled_at
        )

    db.add(
        snapshot
    )

    db.commit()

    db.refresh(
        snapshot
    )

    return snapshot


def _make_event(
    db,
    *,
    old_snapshot: PageSnapshot,
    new_snapshot: PageSnapshot,
    detected_at: datetime | None = None,
) -> SemanticDriftEvent:
    """
    Create one disposable pending vector-analysis event.
    """
    event = SemanticDriftEvent(
        organization_id=(
            old_snapshot.organization_id
        ),
        monitored_page_id=(
            old_snapshot.monitored_page_id
        ),
        old_snapshot_id=(
            old_snapshot.id
        ),
        new_snapshot_id=(
            new_snapshot.id
        ),
        semantic_delta_json={
            "integration_test": True,
            "component": "vector_worker",
        },
        summary_text=(
            "Vector worker integration test."
        ),
        analysis_status="completed",
        risk_level="unknown",
        vector_analysis_status=(
            VECTOR_ANALYSIS_STATUS_PENDING
        ),
    )

    if detected_at is not None:
        event.detected_at = (
            detected_at
        )

    db.add(
        event
    )

    db.commit()

    db.refresh(
        event
    )

    return event


def _delete_test_rows(
    db,
    *,
    event_ids=None,
    snapshot_ids=None,
) -> None:
    """
    Remove disposable test rows.

    Services and worker processing may commit internally,
    so cleanup cannot rely on transaction rollback alone.

    Risk assessments must be deleted before their drift events.
    The RiskAssessment -> SemanticDriftEvent foreign key uses
    ON DELETE SET NULL, so deleting an event first would leave
    committed test assessments behind as orphaned rows.
    """
    db.rollback()

    event_ids = list(
        event_ids
        or []
    )

    if event_ids:
        db.query(
            RiskAssessment
        ).filter(
            RiskAssessment.semantic_drift_event_id.in_(
                event_ids
            )
        ).delete(
            synchronize_session=False
        )

        db.commit()

    for event_id in event_ids:
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

        if event is not None:
            db.delete(
                event
            )

            db.commit()

    for snapshot_id in (
        snapshot_ids
        or []
    ):
        snapshot = (
            db.query(
                PageSnapshot
            )
            .filter(
                PageSnapshot.id
                == snapshot_id
            )
            .one_or_none()
        )

        if snapshot is not None:
            db.delete(
                snapshot
            )

            db.commit()


def test_worker_does_not_select_event_with_pending_embedding():
    db = SessionLocal()

    old_snapshot = None
    new_snapshot = None
    event = None

    try:
        existing = (
            _existing_snapshot(
                db
            )
        )

        old_snapshot = (
            _make_snapshot(
                db,
                existing=existing,
                raw_content=(
                    "Worker pending old content."
                ),
                embedding=_vector(
                    0
                ),
                embedding_status="completed",
            )
        )

        new_snapshot = (
            _make_snapshot(
                db,
                existing=existing,
                raw_content=(
                    "Worker pending new content."
                ),
                embedding=None,
                embedding_status="pending",
            )
        )

        event = (
            _make_event(
                db,
                old_snapshot=old_snapshot,
                new_snapshot=new_snapshot,
            )
        )

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=10,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=True,
        )

        selected_ids = (
            worker._select_event_ids(
                db
            )
        )

        assert (
            str(
                event.id
            )
            not in selected_ids
        )

        stats = (
            worker.process_events()
        )

        db.expire_all()

        persisted = (
            db.query(
                SemanticDriftEvent
            )
            .filter(
                SemanticDriftEvent.id
                == event.id
            )
            .one()
        )

        assert (
            persisted.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_PENDING
        )

        assert (
            persisted.vector_analysis_attempts
            == 0
        )

        assert (
            persisted.semantic_similarity
            is None
        )

        assert (
            persisted.semantic_distance
            is None
        )

        assert (
            persisted.semantic_magnitude
            is None
        )

        # There may be other eligible rows in a developer DB,
        # so do not assert the global batch selected count here.
        assert (
            stats.failed
            == 0
        )

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (
                    event,
                )
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )

        db.close()


def test_worker_selects_and_completes_ready_event():
    db = SessionLocal()

    old_snapshot = None
    new_snapshot = None
    event = None

    try:
        existing = (
            _existing_snapshot(
                db
            )
        )

        old_snapshot = (
            _make_snapshot(
                db,
                existing=existing,
                raw_content=(
                    "Worker completed old content."
                ),
                embedding=_vector(
                    0
                ),
                embedding_status="completed",
            )
        )

        new_snapshot = (
            _make_snapshot(
                db,
                existing=existing,
                raw_content=(
                    "Worker completed new content."
                ),
                embedding=_vector(
                    1
                ),
                embedding_status="completed",
            )
        )

        event = (
            _make_event(
                db,
                old_snapshot=old_snapshot,
                new_snapshot=new_snapshot,
                detected_at=(
                    datetime.now(
                        timezone.utc
                    )
                    - timedelta(
                        days=3650
                    )
                ),
            )
        )

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=True,
        )

        selected_ids = (
            worker._select_event_ids(
                db
            )
        )

        assert selected_ids == [
            str(
                event.id
            )
        ]

        stats = (
            worker.process_events()
        )

        assert (
            stats.selected
            == 1
        )

        assert (
            stats.completed
            == 1
        )

        assert (
            stats.failed
            == 0
        )

        assert (
            stats.deferred
            == 0
        )

        db.expire_all()

        persisted = (
            db.query(
                SemanticDriftEvent
            )
            .filter(
                SemanticDriftEvent.id
                == event.id
            )
            .one()
        )

        assert (
            persisted.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_COMPLETED
        )

        assert (
            persisted.vector_analysis_attempts
            == 1
        )

        assert (
            persisted.vector_analysis_error
            is None
        )

        assert (
            persisted.semantic_similarity
            == pytest.approx(
                0.0,
                abs=1e-7,
            )
        )

        assert (
            persisted.semantic_distance
            == pytest.approx(
                1.0,
                abs=1e-7,
            )
        )

        assert (
            persisted.semantic_magnitude
            == pytest.approx(
                0.5,
                abs=1e-7,
            )
        )

        assert (
            persisted.vector_analyzed_at
            is not None
        )

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (
                    event,
                )
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )

        db.close()


def test_pending_event_does_not_starve_ready_event():
    db = SessionLocal()

    pending_old = None
    pending_new = None
    pending_event = None

    ready_old = None
    ready_new = None
    ready_event = None

    try:
        existing = (
            _existing_snapshot(
                db
            )
        )

        now = datetime.now(
            timezone.utc
        )

        pending_old = (
            _make_snapshot(
                db,
                existing=existing,
                raw_content=(
                    "Starvation pending old."
                ),
                embedding=_vector(
                    0
                ),
                embedding_status="completed",
            )
        )

        pending_new = (
            _make_snapshot(
                db,
                existing=existing,
                raw_content=(
                    "Starvation pending new."
                ),
                embedding=None,
                embedding_status="pending",
            )
        )

        pending_event = (
            _make_event(
                db,
                old_snapshot=pending_old,
                new_snapshot=pending_new,
                detected_at=(
                    now
                    - timedelta(
                        days=3651
                    )
                ),
            )
        )

        ready_old = (
            _make_snapshot(
                db,
                existing=existing,
                raw_content=(
                    "Starvation ready old."
                ),
                embedding=_vector(
                    0
                ),
                embedding_status="completed",
            )
        )

        ready_new = (
            _make_snapshot(
                db,
                existing=existing,
                raw_content=(
                    "Starvation ready new."
                ),
                embedding=_vector(
                    1
                ),
                embedding_status="completed",
            )
        )

        ready_event = (
            _make_event(
                db,
                old_snapshot=ready_old,
                new_snapshot=ready_new,
                detected_at=(
                    now
                    - timedelta(
                        days=3650
                    )
                ),
            )
        )

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=True,
        )

        selected_ids = (
            worker._select_event_ids(
                db
            )
        )

        assert selected_ids == [
            str(
                ready_event.id
            )
        ]

        assert (
            str(
                pending_event.id
            )
            not in selected_ids
        )

        stats = (
            worker.process_events()
        )

        assert (
            stats.selected
            == 1
        )

        assert (
            stats.completed
            == 1
        )

        assert (
            stats.failed
            == 0
        )

        db.expire_all()

        persisted_pending = (
            db.query(
                SemanticDriftEvent
            )
            .filter(
                SemanticDriftEvent.id
                == pending_event.id
            )
            .one()
        )

        persisted_ready = (
            db.query(
                SemanticDriftEvent
            )
            .filter(
                SemanticDriftEvent.id
                == ready_event.id
            )
            .one()
        )

        assert (
            persisted_pending.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_PENDING
        )

        assert (
            persisted_pending.vector_analysis_attempts
            == 0
        )

        assert (
            persisted_ready.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_COMPLETED
        )

        assert (
            persisted_ready.vector_analysis_attempts
            == 1
        )

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (
                    pending_event,
                    ready_event,
                )
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    pending_old,
                    pending_new,
                    ready_old,
                    ready_new,
                )
                if snapshot is not None
            ],
        )

        db.close()

def test_risk_queue_does_not_select_vector_pending_event():
    db = SessionLocal()

    old_snapshot = None
    new_snapshot = None
    event = None

    try:
        existing = _existing_snapshot(
            db
        )

        old_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk pending old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        new_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk pending new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        event = _make_event(
            db,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
        )

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            risk_limit=10,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=False,
            process_risk_scoring=True,
        )

        selected_ids = worker._select_risk_event_ids(
            db
        )

        assert str(event.id) not in selected_ids

        stats = worker.process_risk_events()

        db.expire_all()

        persisted = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id
                == event.id
            )
            .one()
        )

        assert (
            persisted.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_PENDING
        )

        assert persisted.semantic_magnitude is None
        assert persisted.risk_scoring_status == "pending"
        assert persisted.risk_scoring_attempts == 0
        assert persisted.risk_score is None
        assert stats.failed == 0

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (event,)
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )

        db.close()


def test_worker_vector_analysis_flows_into_risk_scoring(
    monkeypatch,
):
    db = SessionLocal()

    old_snapshot = None
    new_snapshot = None
    event = None

    try:
        existing = _existing_snapshot(
            db
        )

        old_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Pipeline old content.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        new_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Pipeline new content.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        event = _make_event(
            db,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
            detected_at=(
                datetime.now(timezone.utc)
                + timedelta(days=3650)
            ),
        )

        event.risk_level = "high"
        event.impact_score = 80
        event.confidence = 0.9

        db.add(event)
        db.commit()
        db.refresh(event)

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            risk_limit=1,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=True,
            process_risk_scoring=True,
            process_pattern_detection=False,
            process_anomaly_detection=False,
        )
        event_id = str(event.id)

        monkeypatch.setattr(
            worker,
            "_select_risk_event_ids",
            lambda db: [event_id],
        )
        stats = worker.run_cycle()

        assert stats.events.selected == 1
        assert stats.events.completed == 1
        assert stats.events.failed == 0

        assert stats.risk_events.selected == 1
        assert stats.risk_events.completed == 1
        assert stats.risk_events.failed == 0

        db.expire_all()

        persisted = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id
                == event.id
            )
            .one()
        )

        assert (
            persisted.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_COMPLETED
        )

        assert persisted.semantic_magnitude == pytest.approx(
            0.5,
            abs=1e-7,
        )

        assert persisted.risk_scoring_status == "completed"
        assert persisted.risk_scoring_attempts == 1

        assert persisted.risk_score is not None
        assert 0.0 <= persisted.risk_score <= 100.0

        assert persisted.risk_score_version == "v2"
        assert persisted.risk_score_components is not None
        assert persisted.risk_scored_at is not None
        assessment = (
            db.query(
                RiskAssessment
            )
            .filter(
                RiskAssessment.organization_id
                == persisted.organization_id,
                RiskAssessment.semantic_drift_event_id
                == persisted.id,
                RiskAssessment.risk_version
                == RISK_VERSION_V2,
            )
            .one()
        )

        assert 0.0 <= assessment.score <= 1.0

        assert persisted.risk_score == pytest.approx(
            assessment.score * 100.0,
            abs=0.01,
        )
        v3_assessment = (
            db.query(
                RiskAssessment
            )
            .filter(
                RiskAssessment.organization_id
                == persisted.organization_id,
                RiskAssessment.semantic_drift_event_id
                == persisted.id,
                RiskAssessment.risk_version
                == RISK_VERSION_V3,
            )
            .one()
        )

        assert 0.0 <= v3_assessment.score <= 1.0

        assert persisted.risk_score_version == "v2"
        assert persisted.risk_scoring_attempts == 1
    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (event,)
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )

        db.close()

def test_worker_cycle_closes_embedding_vector_and_risk_v2_pipeline(
    monkeypatch,
):
    """
    Prove the complete durable worker pipeline in one cycle:

        pending snapshots
            -> deterministic embeddings
            -> vector analysis
            -> topology preparation
            -> Risk v2
            -> persisted RiskAssessment

    Queue selectors are pinned to the disposable rows created by this
    test so unrelated developer-database backlog cannot affect it.
    Selector behavior itself is covered by the focused tests above.
    """
    db = SessionLocal()

    old_snapshot = None
    new_snapshot = None
    event = None

    try:
        existing = _existing_snapshot(
            db
        )

        old_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content=(
                "Full pipeline old knowledge."
            ),
            embedding=None,
            embedding_status="pending",
        )

        new_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content=(
                "Full pipeline changed knowledge."
            ),
            embedding=None,
            embedding_status="pending",
        )

        event = _make_event(
            db,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
            detected_at=(
                datetime.now(timezone.utc)
                + timedelta(days=3654)
            ),
        )

        # Keep the same deterministic risk inputs used by the existing
        # vector -> Risk v2 integration test. The purpose of this test is
        # to add the previously missing embedding stage in front of it.
        event.risk_level = "high"
        event.impact_score = 80
        event.confidence = 0.9

        db.add(event)
        db.commit()
        db.refresh(event)

        old_snapshot_id = str(
            old_snapshot.id
        )
        new_snapshot_id = str(
            new_snapshot.id
        )
        event_id = str(event.id)

        worker = VectorWorker(
            snapshot_limit=2,
            event_limit=1,
            risk_limit=1,
            max_attempts=3,
            process_embeddings=True,
            process_analysis=True,
            process_risk_scoring=True,
            process_pattern_detection=False,
            process_anomaly_detection=False,
        )

        fake_embedding_service = (
            DeterministicEmbeddingService(
                {
                    old_snapshot_id: _vector(0),
                    new_snapshot_id: _vector(1),
                }
            )
        )

        worker.embedding_service = (
            fake_embedding_service
        )

        # Isolate this end-to-end orchestration test from historical
        # pending rows that may already exist in a developer database.
        monkeypatch.setattr(
            worker,
            "_select_snapshot_ids",
            lambda db: [
                old_snapshot_id,
                new_snapshot_id,
            ],
        )

        monkeypatch.setattr(
            worker,
            "_select_event_ids",
            lambda db: [event_id],
        )

        monkeypatch.setattr(
            worker,
            "_select_risk_event_ids",
            lambda db: [event_id],
        )

        stats = worker.run_cycle()

        assert stats.snapshots.selected == 2
        assert stats.snapshots.completed == 2
        assert stats.snapshots.failed == 0
        assert stats.snapshots.deferred == 0

        assert (
            fake_embedding_service.called_snapshot_ids
            == [
                old_snapshot_id,
                new_snapshot_id,
            ]
        )

        assert stats.events.selected == 1
        assert stats.events.completed == 1
        assert stats.events.failed == 0
        assert stats.events.deferred == 0

        assert stats.risk_events.selected == 1
        assert stats.risk_events.completed == 1
        assert stats.risk_events.failed == 0
        assert stats.risk_events.deferred == 0

        db.expire_all()

        persisted_old = (
            db.query(PageSnapshot)
            .filter(
                PageSnapshot.id
                == old_snapshot.id
            )
            .one()
        )

        persisted_new = (
            db.query(PageSnapshot)
            .filter(
                PageSnapshot.id
                == new_snapshot.id
            )
            .one()
        )

        for persisted_snapshot in (
            persisted_old,
            persisted_new,
        ):
            assert (
                persisted_snapshot.embedding_status
                == "completed"
            )

            assert (
                persisted_snapshot.embedding_attempts
                == 1
            )

            assert (
                persisted_snapshot.embedding
                is not None
            )

            assert (
                persisted_snapshot.embedding_model
                == TEST_EMBEDDING_MODEL
            )

            assert (
                persisted_snapshot.embedding_dimensions
                == EMBEDDING_DIMENSIONS
            )

            assert (
                persisted_snapshot.embedded_at
                is not None
            )

            assert (
                persisted_snapshot.embedding_error
                is None
            )

        persisted_event = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id
                == event.id
            )
            .one()
        )

        assert (
            persisted_event.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_COMPLETED
        )

        assert (
            persisted_event.vector_analysis_attempts
            == 1
        )

        assert (
            persisted_event.vector_analysis_error
            is None
        )

        assert (
            persisted_event.semantic_similarity
            == pytest.approx(
                0.0,
                abs=1e-7,
            )
        )

        assert (
            persisted_event.semantic_distance
            == pytest.approx(
                1.0,
                abs=1e-7,
            )
        )

        assert (
            persisted_event.semantic_magnitude
            == pytest.approx(
                0.5,
                abs=1e-7,
            )
        )

        assert (
            persisted_event.vector_analyzed_at
            is not None
        )

        assert (
            persisted_event.risk_scoring_status
            == "completed"
        )

        assert (
            persisted_event.risk_scoring_attempts
            == 1
        )

        assert (
            persisted_event.risk_score
            is not None
        )

        assert (
            0.0
            <= persisted_event.risk_score
            <= 100.0
        )

        assert (
            persisted_event.risk_score_version
            == "v2"
        )

        assert (
            persisted_event.risk_score_components
            is not None
        )

        assert (
            persisted_event.risk_scored_at
            is not None
        )

        assessment = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == persisted_event.organization_id,
                RiskAssessment.semantic_drift_event_id
                == persisted_event.id,
                RiskAssessment.risk_version
                == RISK_VERSION_V2,
            )
            .one()
        )

        assert (
            0.0
            <= assessment.score
            <= 1.0
        )

        assert (
            persisted_event.risk_score
            == pytest.approx(
                assessment.score * 100.0,
                abs=0.01,
            )
        )

        # P2D: query actual persisted rows, not the worker's return counters.
        from src.services.pipeline_effectiveness_evidence import (
            read_pipeline_event_evidence,
        )
        evidence = read_pipeline_event_evidence(
            db,
            organization_id=persisted_event.organization_id,
            event_id=persisted_event.id,
        )
        assert evidence["gates"]["vector_risk_complete"] is True, json.dumps(evidence, sort_keys=True, default=str)
        assert evidence["measurements"]["risk_v2_score"] == pytest.approx(
            assessment.score
        )
        assert evidence["stages"]["causal_decisions"] == "not_observed"
        with pytest.raises(LookupError):
            read_pipeline_event_evidence(
                db, organization_id=uuid.uuid4(), event_id=persisted_event.id
            )
        print("P2D_VECTOR_RISK_EVIDENCE=" + json.dumps(evidence, sort_keys=True))
        # Vector/Risk evidence is not proof of downstream physical execution.

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (event,)
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )

        db.close()

def test_vector_pending_event_does_not_starve_risk_ready_event():
    db = SessionLocal()

    pending_old = None
    pending_new = None
    pending_event = None

    ready_old = None
    ready_new = None
    ready_event = None

    try:
        existing = _existing_snapshot(
            db
        )

        now = datetime.now(
            timezone.utc
        )

        pending_old = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk starvation pending old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        pending_new = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk starvation pending new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        pending_event = _make_event(
            db,
            old_snapshot=pending_old,
            new_snapshot=pending_new,
            detected_at=(
                now
                + timedelta(days=3655)
            ),
        )

        ready_old = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk starvation ready old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        ready_new = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk starvation ready new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        ready_event = _make_event(
            db,
            old_snapshot=ready_old,
            new_snapshot=ready_new,
            detected_at=(
                now
                + timedelta(days=3656)
            ),
        )

        ready_event.vector_analysis_status = (
            VECTOR_ANALYSIS_STATUS_COMPLETED
        )
        ready_event.semantic_similarity = 0.0
        ready_event.semantic_distance = 1.0
        ready_event.semantic_magnitude = 0.5
        ready_event.risk_level = "medium"
        ready_event.impact_score = 55
        ready_event.confidence = 0.8

        db.add(ready_event)
        db.commit()
        db.refresh(ready_event)

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            risk_limit=1,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=False,
            process_risk_scoring=True,
        )

        selected_ids = worker._select_risk_event_ids(
            db
        )

        assert selected_ids == [
            str(ready_event.id)
        ]

        assert (
            str(pending_event.id)
            not in selected_ids
        )

        stats = worker.process_risk_events()

        assert stats.selected == 1
        assert stats.completed == 1
        assert stats.failed == 0

        db.expire_all()

        persisted_pending = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id
                == pending_event.id
            )
            .one()
        )

        persisted_ready = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id
                == ready_event.id
            )
            .one()
        )

        assert (
            persisted_pending.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_PENDING
        )
        assert persisted_pending.risk_scoring_status == "pending"
        assert persisted_pending.risk_scoring_attempts == 0

        assert (
            persisted_ready.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_COMPLETED
        )
        assert persisted_ready.risk_scoring_status == "completed"
        assert persisted_ready.risk_scoring_attempts == 1
        assert persisted_ready.risk_score is not None

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (
                    pending_event,
                    ready_event,
                )
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    pending_old,
                    pending_new,
                    ready_old,
                    ready_new,
                )
                if snapshot is not None
            ],
        )

        db.close()

def test_risk_v2_backfills_completed_v1_event():
    db = SessionLocal()

    old_snapshot = None
    new_snapshot = None
    event = None

    try:
        existing = _existing_snapshot(
            db
        )

        old_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk v2 backfill old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        new_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk v2 backfill new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        event = _make_event(
            db,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
            detected_at=(
                datetime.now(timezone.utc)
                + timedelta(days=3657)
            ),
        )

        event.vector_analysis_status = (
            VECTOR_ANALYSIS_STATUS_COMPLETED
        )
        event.semantic_similarity = 0.0
        event.semantic_distance = 1.0
        event.semantic_magnitude = 0.5

        event.risk_level = "high"
        event.impact_score = 80
        event.confidence = 0.9

        event.risk_scoring_status = (
            "completed"
        )
        event.risk_scoring_attempts = 1
        event.risk_score = 70.0
        event.risk_score_version = "v1"
        event.risk_score_components = {
            "legacy": True,
        }
        event.risk_scored_at = (
            datetime.now(timezone.utc)
        )

        db.add(event)
        db.commit()
        db.refresh(event)

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            risk_limit=1,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=False,
            process_risk_scoring=True,
        )

        selected_ids = (
            worker._select_risk_event_ids(
                db
            )
        )

        assert selected_ids == [
            str(event.id)
        ]

        stats = (
            worker.process_risk_events()
        )

        assert stats.selected == 1
        assert stats.completed == 1
        assert stats.failed == 0

        db.expire_all()

        persisted = (
            db.query(
                SemanticDriftEvent
            )
            .filter(
                SemanticDriftEvent.id
                == event.id
            )
            .one()
        )

        assert (
            persisted.risk_score_version
            == "v2"
        )

        assert (
            persisted.risk_scoring_status
            == "completed"
        )

        assessment = (
            db.query(
                RiskAssessment
            )
            .filter(
                RiskAssessment.organization_id
                == persisted.organization_id,
                RiskAssessment
                .semantic_drift_event_id
                == persisted.id,
                RiskAssessment.risk_version
                == RISK_VERSION_V2,
            )
            .one()
        )

        assert 0.0 <= assessment.score <= 1.0

        assert persisted.risk_score == pytest.approx(
            assessment.score * 100.0,
            abs=0.01,
        )

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (event,)
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )

        db.close()


def test_risk_v3_shadow_backfills_without_mutating_v2_projection(
    monkeypatch,
):
    db = SessionLocal()

    old_snapshot = None
    new_snapshot = None
    event = None

    try:
        existing = _existing_snapshot(
            db
        )

        old_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk v3 shadow backfill old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        new_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk v3 shadow backfill new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        event = _make_event(
            db,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
            detected_at=(
                datetime.now(timezone.utc)
                + timedelta(days=3651)
            ),
        )

        event.vector_analysis_status = (
            VECTOR_ANALYSIS_STATUS_COMPLETED
        )
        event.semantic_similarity = 0.0
        event.semantic_distance = 1.0
        event.semantic_magnitude = 0.5
        event.risk_level = "high"
        event.impact_score = 80
        event.confidence = 0.9

        db.add(event)
        db.commit()
        db.refresh(event)

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            risk_limit=1000,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=False,
            process_risk_scoring=True,
        )

        event_id = str(event.id)
        original_selector = worker._select_risk_event_ids
        monkeypatch.setattr(
            worker,
            "_select_risk_event_ids",
            lambda db: [event_id],
        )

        first_stats = worker.process_risk_events()

        monkeypatch.setattr(
            worker,
            "_select_risk_event_ids",
            original_selector,
        )

        assert first_stats.selected == 1
        assert first_stats.completed == 1
        assert first_stats.failed == 0
        assert first_stats.deferred == 0

        db.expire_all()

        projected = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id
                == event.id
            )
            .one()
        )

        v2_assessment = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == projected.organization_id,
                RiskAssessment.semantic_drift_event_id
                == projected.id,
                RiskAssessment.risk_version
                == RISK_VERSION_V2,
            )
            .one()
        )

        v3_assessment = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == projected.organization_id,
                RiskAssessment.semantic_drift_event_id
                == projected.id,
                RiskAssessment.risk_version
                == RISK_VERSION_V3,
            )
            .one()
        )

        v2_assessment_id = v2_assessment.id

        projection_before = {
            "risk_score": projected.risk_score,
            "risk_score_version": projected.risk_score_version,
            "risk_score_components": projected.risk_score_components,
            "risk_scoring_status": projected.risk_scoring_status,
            "risk_scoring_attempts": projected.risk_scoring_attempts,
            "risk_scored_at": projected.risk_scored_at,
        }

        db.delete(v3_assessment)
        db.commit()
        db.expire_all()

        selected_ids = worker._select_risk_event_ids(
            db
        )

        assert event_id in selected_ids

        monkeypatch.setattr(
            worker,
            "_select_risk_event_ids",
            lambda db: [event_id],
        )

        second_stats = worker.process_risk_events()

        monkeypatch.setattr(
            worker,
            "_select_risk_event_ids",
            original_selector,
        )

        assert second_stats.selected == 1
        assert second_stats.completed == 1
        assert second_stats.failed == 0
        assert second_stats.deferred == 0

        db.expire_all()

        persisted = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id
                == event.id
            )
            .one()
        )

        persisted_v2 = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == persisted.organization_id,
                RiskAssessment.semantic_drift_event_id
                == persisted.id,
                RiskAssessment.risk_version
                == RISK_VERSION_V2,
            )
            .one()
        )

        persisted_v3 = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == persisted.organization_id,
                RiskAssessment.semantic_drift_event_id
                == persisted.id,
                RiskAssessment.risk_version
                == RISK_VERSION_V3,
            )
            .one()
        )

        assert persisted_v2.id == v2_assessment_id
        assert persisted_v3.id is not None

        assert persisted.risk_score == projection_before[
            "risk_score"
        ]
        assert (
            persisted.risk_score_version
            == projection_before["risk_score_version"]
            == "v2"
        )
        assert (
            persisted.risk_score_components
            == projection_before["risk_score_components"]
        )
        assert (
            persisted.risk_scoring_status
            == projection_before["risk_scoring_status"]
            == "completed"
        )
        assert (
            persisted.risk_scoring_attempts
            == projection_before["risk_scoring_attempts"]
            == 1
        )
        assert (
            persisted.risk_scored_at
            == projection_before["risk_scored_at"]
        )

        selected_after_backfill = (
            worker._select_risk_event_ids(
                db
            )
        )

        assert event_id not in selected_after_backfill

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (event,)
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )

        db.close()


def test_risk_v3_shadow_failure_rolls_back_v2_transaction(
    monkeypatch,
):
    db = SessionLocal()

    old_snapshot = None
    new_snapshot = None
    event = None

    try:
        existing = _existing_snapshot(db)

        old_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk v3 rollback old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        new_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk v3 rollback new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        event = _make_event(
            db,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
            detected_at=(
                datetime.now(timezone.utc)
                + timedelta(days=3652)
            ),
        )

        event.vector_analysis_status = (
            VECTOR_ANALYSIS_STATUS_COMPLETED
        )
        event.semantic_similarity = 0.0
        event.semantic_distance = 1.0
        event.semantic_magnitude = 0.5
        event.risk_level = "high"
        event.impact_score = 80
        event.confidence = 0.9

        db.add(event)
        db.commit()
        db.refresh(event)

        projection_before = {
            "risk_score": event.risk_score,
            "risk_score_version": event.risk_score_version,
            "risk_score_components": event.risk_score_components,
            "risk_scoring_status": event.risk_scoring_status,
            "risk_scoring_attempts": event.risk_scoring_attempts,
            "risk_scored_at": event.risk_scored_at,
        }

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            risk_limit=1,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=False,
            process_risk_scoring=True,
        )

        event_id = str(event.id)

        monkeypatch.setattr(
            worker,
            "_select_risk_event_ids",
            lambda db: [event_id],
        )

        def fail_v3_shadow(*args, **kwargs):
            raise RuntimeError(
                "synthetic Risk v3 shadow failure"
            )

        monkeypatch.setattr(
            worker.risk_scoring_v3_shadow_service,
            "score_event",
            fail_v3_shadow,
        )

        stats = worker.process_risk_events()

        assert stats.selected == 1
        assert stats.completed == 0
        assert stats.failed == 1
        assert stats.deferred == 0

        db.expire_all()

        persisted = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id == event.id
            )
            .one()
        )

        assessments = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == persisted.organization_id,
                RiskAssessment.semantic_drift_event_id
                == persisted.id,
                RiskAssessment.risk_version.in_(
                    [
                        RISK_VERSION_V2,
                        RISK_VERSION_V3,
                    ]
                ),
            )
            .all()
        )

        assert assessments == []

        assert (
            persisted.risk_score
            == projection_before["risk_score"]
        )
        assert (
            persisted.risk_score_version
            == projection_before["risk_score_version"]
        )
        assert (
            persisted.risk_score_components
            == projection_before["risk_score_components"]
        )
        assert (
            persisted.risk_scoring_status
            == projection_before["risk_scoring_status"]
        )
        assert (
            persisted.risk_scoring_attempts
            == projection_before["risk_scoring_attempts"]
        )
        assert (
            persisted.risk_scored_at
            == projection_before["risk_scored_at"]
        )

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (event,)
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )

        db.close()


def test_failed_risk_event_does_not_starve_later_ready_event(
    monkeypatch,
):
    db = SessionLocal()

    poison_old = None
    poison_new = None
    poison_event = None

    healthy_old = None
    healthy_new = None
    healthy_event = None

    try:
        existing = _existing_snapshot(db)

        now = datetime.now(timezone.utc)

        poison_old = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk starvation poison old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        poison_new = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk starvation poison new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        poison_event = _make_event(
            db,
            old_snapshot=poison_old,
            new_snapshot=poison_new,
            detected_at=now + timedelta(days=3650),
        )

        healthy_old = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk starvation healthy old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        healthy_new = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk starvation healthy new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        healthy_event = _make_event(
            db,
            old_snapshot=healthy_old,
            new_snapshot=healthy_new,
            detected_at=now + timedelta(days=3651),
        )

        for event in (
            poison_event,
            healthy_event,
        ):
            event.vector_analysis_status = (
                VECTOR_ANALYSIS_STATUS_COMPLETED
            )
            event.semantic_similarity = 0.0
            event.semantic_distance = 1.0
            event.semantic_magnitude = 0.5
            event.risk_level = "high"
            event.impact_score = 80
            event.confidence = 0.9
            db.add(event)

        db.commit()
        db.refresh(poison_event)
        db.refresh(healthy_event)

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            risk_limit=1,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=False,
            process_risk_scoring=True,
        )

        poison_event_id = str(poison_event.id)
        healthy_event_id = str(healthy_event.id)

        original_score_event = (
            worker.risk_scoring_v3_shadow_service.score_event
        )

        def fail_poison_v3(db, event):
            if str(event.id) == poison_event_id:
                raise RuntimeError(
                    "synthetic poison Risk v3 shadow failure"
                )

            return original_score_event(
                db,
                event,
            )

        monkeypatch.setattr(
            worker.risk_scoring_v3_shadow_service,
            "score_event",
            fail_poison_v3,
        )

        first_selected_ids = worker._select_risk_event_ids(
            db
        )

        assert first_selected_ids == [
            poison_event_id
        ]

        first_stats = worker.process_risk_events()

        assert first_stats.selected == 1
        assert first_stats.completed == 0
        assert first_stats.failed == 1
        assert first_stats.deferred == 0

        second_selected_ids = worker._select_risk_event_ids(
            db
        )

        assert second_selected_ids == [
            healthy_event_id
        ]

        second_stats = worker.process_risk_events()

        assert second_stats.selected == 1
        assert second_stats.completed == 1
        assert second_stats.failed == 0
        assert second_stats.deferred == 0

        db.expire_all()

        poison_assessments = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == poison_event.organization_id,
                RiskAssessment.semantic_drift_event_id
                == poison_event.id,
                RiskAssessment.risk_version.in_(
                    [
                        RISK_VERSION_V2,
                        RISK_VERSION_V3,
                    ]
                ),
            )
            .all()
        )

        healthy_assessments = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == healthy_event.organization_id,
                RiskAssessment.semantic_drift_event_id
                == healthy_event.id,
                RiskAssessment.risk_version.in_(
                    [
                        RISK_VERSION_V2,
                        RISK_VERSION_V3,
                    ]
                ),
            )
            .all()
        )

        assert poison_assessments == []
        assert {
            assessment.risk_version
            for assessment in healthy_assessments
        } == {
            RISK_VERSION_V2,
            RISK_VERSION_V3,
        }

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (
                    poison_event,
                    healthy_event,
                )
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    poison_old,
                    poison_new,
                    healthy_old,
                    healthy_new,
                )
                if snapshot is not None
            ],
        )

        db.close()


def test_not_ready_risk_event_does_not_starve_later_ready_event(
    monkeypatch,
):
    db = SessionLocal()

    deferred_old = None
    deferred_new = None
    deferred_event = None

    healthy_old = None
    healthy_new = None
    healthy_event = None

    try:
        existing = _existing_snapshot(db)

        now = datetime.now(timezone.utc)

        deferred_old = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk deferred starvation old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        deferred_new = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk deferred starvation new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        deferred_event = _make_event(
            db,
            old_snapshot=deferred_old,
            new_snapshot=deferred_new,
            detected_at=now + timedelta(days=3652),
        )

        healthy_old = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk deferred healthy old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        healthy_new = _make_snapshot(
            db,
            existing=existing,
            raw_content="Risk deferred healthy new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        healthy_event = _make_event(
            db,
            old_snapshot=healthy_old,
            new_snapshot=healthy_new,
            detected_at=now + timedelta(days=3653),
        )

        for event in (
            deferred_event,
            healthy_event,
        ):
            event.vector_analysis_status = (
                VECTOR_ANALYSIS_STATUS_COMPLETED
            )
            event.semantic_similarity = 0.0
            event.semantic_distance = 1.0
            event.semantic_magnitude = 0.5
            event.risk_level = "high"
            event.impact_score = 80
            event.confidence = 0.9
            db.add(event)

        db.commit()
        db.refresh(deferred_event)
        db.refresh(healthy_event)

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            risk_limit=1,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=False,
            process_risk_scoring=True,
        )

        deferred_event_id = str(deferred_event.id)
        healthy_event_id = str(healthy_event.id)

        original_score_event = (
            worker.risk_scoring_v3_shadow_service.score_event
        )

        def defer_first_event(db, event):
            if str(event.id) == deferred_event_id:
                raise RiskScoringV3ShadowNotReadyError(
                    "synthetic Risk v3 dependency not ready"
                )

            return original_score_event(
                db,
                event,
            )

        monkeypatch.setattr(
            worker.risk_scoring_v3_shadow_service,
            "score_event",
            defer_first_event,
        )

        first_selected_ids = worker._select_risk_event_ids(
            db
        )

        assert first_selected_ids == [
            deferred_event_id
        ]

        first_stats = worker.process_risk_events()

        assert first_stats.selected == 1
        assert first_stats.completed == 0
        assert first_stats.failed == 0
        assert first_stats.deferred == 1

        db.expire_all()

        persisted_deferred_event = db.get(
            SemanticDriftEvent,
            deferred_event.id,
        )

        assert persisted_deferred_event is not None
        assert persisted_deferred_event.risk_retry_after is not None

        second_selected_ids = worker._select_risk_event_ids(
            db
        )

        assert second_selected_ids == [
            healthy_event_id
        ]

        second_stats = worker.process_risk_events()

        assert second_stats.selected == 1
        assert second_stats.completed == 1
        assert second_stats.failed == 0
        assert second_stats.deferred == 0

        db.expire_all()

        deferred_assessments = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == deferred_event.organization_id,
                RiskAssessment.semantic_drift_event_id
                == deferred_event.id,
                RiskAssessment.risk_version.in_(
                    [
                        RISK_VERSION_V2,
                        RISK_VERSION_V3,
                    ]
                ),
            )
            .all()
        )

        healthy_assessments = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == healthy_event.organization_id,
                RiskAssessment.semantic_drift_event_id
                == healthy_event.id,
                RiskAssessment.risk_version.in_(
                    [
                        RISK_VERSION_V2,
                        RISK_VERSION_V3,
                    ]
                ),
            )
            .all()
        )

        assert deferred_assessments == []
        assert {
            assessment.risk_version
            for assessment in healthy_assessments
        } == {
            RISK_VERSION_V2,
            RISK_VERSION_V3,
        }

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (
                    deferred_event,
                    healthy_event,
                )
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    deferred_old,
                    deferred_new,
                    healthy_old,
                    healthy_new,
                )
                if snapshot is not None
            ],
        )

        db.close()


def test_embedding_provider_failure_opens_batch_circuit(
    monkeypatch,
):
    db = SessionLocal()

    snapshot_ids: list[str] = []

    try:
        existing = _existing_snapshot(
            db
        )

        now = datetime.now(
            timezone.utc
        )

        snapshots = [
            _make_snapshot(
                db,
                existing=existing,
                raw_content=(
                    f"Provider circuit snapshot {index}"
                ),
                embedding=None,
                embedding_status="pending",
                crawled_at=(
                    now
                    + timedelta(
                        seconds=index
                    )
                ),
            )
            for index in range(3)
        ]

        snapshot_ids = [
            str(snapshot.id)
            for snapshot in snapshots
        ]

        worker = VectorWorker(
            snapshot_limit=3,
            event_limit=1,
            risk_limit=1,
            max_attempts=3,
            process_embeddings=True,
            process_analysis=False,
            process_risk_scoring=False,
        )

        fake_service = (
            ProviderUnavailableEmbeddingService()
        )

        worker.embedding_service = (
            fake_service
        )

        monkeypatch.setattr(
            worker,
            "_select_snapshot_ids",
            lambda db: list(
                snapshot_ids
            ),
        )

        stats = (
            worker.process_snapshots()
        )

        assert stats.selected == 3
        assert stats.completed == 0
        assert stats.failed == 1
        assert stats.deferred == 2

        assert (
            fake_service.called_snapshot_ids
            == [
                snapshot_ids[0]
            ]
        )

        db.expire_all()

        persisted = (
            db.query(PageSnapshot)
            .filter(
                PageSnapshot.id.in_(
                    snapshot_ids
                )
            )
            .all()
        )

        by_id = {
            str(snapshot.id): snapshot
            for snapshot in persisted
        }

        first = by_id[
            snapshot_ids[0]
        ]

        assert (
            first.embedding_status
            == "failed"
        )
        assert (
            first.embedding_attempts
            == 1
        )

        for snapshot_id in (
            snapshot_ids[1:]
        ):
            untouched = by_id[
                snapshot_id
            ]

            assert (
                untouched.embedding_status
                == "pending"
            )
            assert (
                untouched.embedding_attempts
                == 0
            )
            assert (
                untouched.embedding
                is None
            )

    finally:
        _delete_test_rows(
            db,
            snapshot_ids=snapshot_ids,
        )
        db.close()

def test_snapshot_specific_failure_does_not_open_batch_circuit(
    monkeypatch,
):
    db = SessionLocal()

    snapshot_ids: list[str] = []

    try:
        existing = _existing_snapshot(
            db
        )

        now = datetime.now(
            timezone.utc
        )

        snapshots = [
            _make_snapshot(
                db,
                existing=existing,
                raw_content=(
                    f"Snapshot failure test {index}"
                ),
                embedding=None,
                embedding_status="pending",
                crawled_at=(
                    now
                    + timedelta(
                        seconds=index
                    )
                ),
            )
            for index in range(2)
        ]

        snapshot_ids = [
            str(snapshot.id)
            for snapshot in snapshots
        ]

        worker = VectorWorker(
            snapshot_limit=2,
            event_limit=1,
            risk_limit=1,
            max_attempts=3,
            process_embeddings=True,
            process_analysis=False,
            process_risk_scoring=False,
        )

        fake_service = (
            SnapshotSpecificFailureEmbeddingService()
        )

        worker.embedding_service = (
            fake_service
        )

        monkeypatch.setattr(
            worker,
            "_select_snapshot_ids",
            lambda db: list(
                snapshot_ids
            ),
        )

        stats = (
            worker.process_snapshots()
        )

        assert stats.selected == 2
        assert stats.completed == 1
        assert stats.failed == 1
        assert stats.deferred == 0

        assert (
            fake_service.called_snapshot_ids
            == snapshot_ids
        )

        db.expire_all()

        persisted = (
            db.query(PageSnapshot)
            .filter(
                PageSnapshot.id.in_(
                    snapshot_ids
                )
            )
            .all()
        )

        by_id = {
            str(snapshot.id): snapshot
            for snapshot in persisted
        }

        failed_snapshot = by_id[
            snapshot_ids[0]
        ]

        completed_snapshot = by_id[
            snapshot_ids[1]
        ]

        assert (
            failed_snapshot.embedding_status
            == "failed"
        )
        assert (
            failed_snapshot.embedding_attempts
            == 1
        )

        assert (
            completed_snapshot.embedding_status
            == "completed"
        )
        assert (
            completed_snapshot.embedding_attempts
            == 1
        )
        assert (
            completed_snapshot.embedding
            is not None
        )

    finally:
        _delete_test_rows(
            db,
            snapshot_ids=snapshot_ids,
        )
        db.close()


def test_concurrent_risk_workers_do_not_repeat_v2_projection(
    monkeypatch,
):
    db = SessionLocal()

    old_snapshot = None
    new_snapshot = None
    event = None

    try:
        existing = _existing_snapshot(db)

        old_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Concurrent risk worker old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        new_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Concurrent risk worker new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        event = _make_event(
            db,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
            detected_at=(
                datetime.now(timezone.utc)
                + timedelta(days=3653)
            ),
        )

        event.vector_analysis_status = (
            VECTOR_ANALYSIS_STATUS_COMPLETED
        )
        event.semantic_similarity = 0.0
        event.semantic_distance = 1.0
        event.semantic_magnitude = 0.5
        event.risk_level = "high"
        event.impact_score = 80
        event.confidence = 0.9

        db.add(event)
        db.commit()
        db.refresh(event)

        event_id = str(event.id)

        start_barrier = Barrier(2)

        def run_worker():
            worker = VectorWorker(
                snapshot_limit=1,
                event_limit=1,
                risk_limit=1,
                max_attempts=3,
                process_embeddings=False,
                process_analysis=False,
                process_risk_scoring=True,
            )

            worker._select_risk_event_ids = (
                lambda db: [event_id]
            )

            start_barrier.wait(
                timeout=10
            )

            return worker.process_risk_events()

        with ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            futures = [
                executor.submit(run_worker)
                for _ in range(2)
            ]

            results = [
                future.result(timeout=30)
                for future in futures
            ]

        assert sum(
            result.selected
            for result in results
        ) == 2

        assert sum(
            result.completed
            for result in results
        ) == 1

        assert sum(
            result.failed
            for result in results
        ) == 0

        assert sum(
            result.deferred
            for result in results
        ) == 0

        db.expire_all()

        persisted = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id
                == event.id
            )
            .one()
        )

        assessments = (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == persisted.organization_id,
                RiskAssessment.semantic_drift_event_id
                == persisted.id,
                RiskAssessment.risk_version.in_(
                    [
                        RISK_VERSION_V2,
                        RISK_VERSION_V3,
                    ]
                ),
            )
            .all()
        )

        assert len(assessments) == 2

        assert {
            assessment.risk_version
            for assessment in assessments
        } == {
            RISK_VERSION_V2,
            RISK_VERSION_V3,
        }

        assert (
            persisted.risk_score_version
            == "v2"
        )
        assert (
            persisted.risk_scoring_status
            == "completed"
        )
        assert (
            persisted.risk_scoring_attempts
            == 1
        )
        assert persisted.risk_score is not None
        assert persisted.risk_scored_at is not None

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (event,)
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )
        db.close()


def test_temporal_invalidation_queue_requires_completed_evidence():
    db = SessionLocal()

    old_snapshot = None
    new_snapshot = None
    event = None

    try:
        existing = _existing_snapshot(db)

        old_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Temporal readiness old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        new_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Temporal readiness new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        event = _make_event(
            db,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
            detected_at=(
                datetime.now(timezone.utc)
                - timedelta(days=3650)
            ),
        )

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            temporal_invalidation_limit=1000,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=False,
            process_temporal_invalidation=True,
            process_risk_scoring=False,
        )

        event_id = str(event.id)

        selected_before = (
            worker._select_temporal_invalidation_event_ids(
                db
            )
        )

        assert event_id not in selected_before

        event.vector_analysis_status = (
            VECTOR_ANALYSIS_STATUS_COMPLETED
        )
        event.semantic_similarity = 0.0
        event.semantic_distance = 1.0
        event.semantic_magnitude = 0.5

        db.add(event)
        db.commit()
        db.refresh(event)

        selected_after = (
            worker._select_temporal_invalidation_event_ids(
                db
            )
        )

        assert event_id in selected_after

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (event,)
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )
        db.close()


def test_temporal_invalidation_zero_root_completion_is_not_rediscovered(
    monkeypatch,
):
    db = SessionLocal()

    old_snapshot = None
    new_snapshot = None
    event = None

    try:
        existing = _existing_snapshot(db)

        old_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Temporal zero-root old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        new_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Temporal zero-root new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        event = _make_event(
            db,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
        )

        event.vector_analysis_status = (
            VECTOR_ANALYSIS_STATUS_COMPLETED
        )
        event.semantic_similarity = 0.0
        event.semantic_distance = 1.0
        event.semantic_magnitude = 0.5

        db.add(event)
        db.commit()
        db.refresh(event)

        event_id = str(event.id)
        calls: list[str] = []

        def analyze_zero_root(
            db,
            *,
            organization_id,
            event_id,
            policy_identity,
        ):
            calls.append(str(event_id))

            class Result:
                roots = ()
                analyses = ()

            return Result()

        monkeypatch.setattr(
            vector_worker_module,
            "analyze_temporal_lineage_invalidation",
            analyze_zero_root,
        )

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            temporal_invalidation_limit=1,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=False,
            process_temporal_invalidation=True,
            process_risk_scoring=False,
        )

        monkeypatch.setattr(
            worker,
            "_select_temporal_invalidation_event_ids",
            lambda db: [event_id],
        )

        stats = worker.process_temporal_invalidation_events()

        assert stats.selected == 1
        assert stats.completed == 1
        assert stats.failed == 0
        assert stats.deferred == 0
        assert calls == [event_id]

        db.expire_all()

        persisted = db.get(
            SemanticDriftEvent,
            event.id,
        )

        assert persisted is not None
        assert (
            persisted.temporal_invalidation_status
            == TEMPORAL_INVALIDATION_STATUS_COMPLETED
        )
        assert persisted.temporal_invalidation_attempts == 1
        assert persisted.temporal_invalidation_error is None
        assert (
            persisted.temporal_invalidation_analyzed_at
            is not None
        )

        monkeypatch.undo()

        selected_after = (
            worker._select_temporal_invalidation_event_ids(
                db
            )
        )

        assert event_id not in selected_after

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (event,)
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )
        db.close()


def test_temporal_invalidation_failure_is_retryable(
    monkeypatch,
):
    db = SessionLocal()

    old_snapshot = None
    new_snapshot = None
    event = None

    try:
        existing = _existing_snapshot(db)

        old_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Temporal retry old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        new_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Temporal retry new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        event = _make_event(
            db,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
        )

        event.vector_analysis_status = (
            VECTOR_ANALYSIS_STATUS_COMPLETED
        )
        event.semantic_similarity = 0.0
        event.semantic_distance = 1.0
        event.semantic_magnitude = 0.5

        db.add(event)
        db.commit()
        db.refresh(event)

        event_id = str(event.id)
        call_count = 0

        def fail_then_succeed(
            db,
            *,
            organization_id,
            event_id,
            policy_identity,
        ):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                raise RuntimeError(
                    "synthetic temporal invalidation failure"
                )

            class Result:
                roots = ()
                analyses = ()

            return Result()

        monkeypatch.setattr(
            vector_worker_module,
            "analyze_temporal_lineage_invalidation",
            fail_then_succeed,
        )

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            temporal_invalidation_limit=1,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=False,
            process_temporal_invalidation=True,
            process_risk_scoring=False,
        )

        monkeypatch.setattr(
            worker,
            "_select_temporal_invalidation_event_ids",
            lambda db: [event_id],
        )

        first_stats = (
            worker.process_temporal_invalidation_events()
        )

        assert first_stats.selected == 1
        assert first_stats.completed == 0
        assert first_stats.failed == 1
        assert first_stats.deferred == 0

        db.expire_all()

        failed = db.get(
            SemanticDriftEvent,
            event.id,
        )

        assert failed is not None
        assert (
            failed.temporal_invalidation_status
            == TEMPORAL_INVALIDATION_STATUS_FAILED
        )
        assert failed.temporal_invalidation_attempts == 1
        assert (
            failed.temporal_invalidation_error
            == "synthetic temporal invalidation failure"
        )
        assert failed.temporal_invalidation_analyzed_at is None

        second_stats = (
            worker.process_temporal_invalidation_events()
        )

        assert second_stats.selected == 1
        assert second_stats.completed == 1
        assert second_stats.failed == 0
        assert second_stats.deferred == 0
        assert call_count == 2

        db.expire_all()

        completed = db.get(
            SemanticDriftEvent,
            event.id,
        )

        assert completed is not None
        assert (
            completed.temporal_invalidation_status
            == TEMPORAL_INVALIDATION_STATUS_COMPLETED
        )
        assert completed.temporal_invalidation_attempts == 2
        assert completed.temporal_invalidation_error is None
        assert (
            completed.temporal_invalidation_analyzed_at
            is not None
        )

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (event,)
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )
        db.close()


def test_temporal_invalidation_worker_persists_real_decision_and_provenance():
    db = SessionLocal()

    old_snapshot = None
    new_snapshot = None
    event = None
    observation_ids: list[uuid.UUID] = []

    try:
        existing = _existing_snapshot(db)

        old_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Production path temporal old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        new_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Production path temporal new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        root = persist_lineage_observation(
            db,
            organization_id=old_snapshot.organization_id,
            observation=LineageObservation(
                observation_kind="materialization",
                source_external_id="worker-production-source",
                snapshot_external_id="worker-production-snapshot-v1",
                asset_external_id="worker-production-chunk",
                asset_kind="chunk",
                revision_external_id="worker-production-chunk@v1",
                sequence=1,
            ),
        )

        observation_ids.append(root.record.id)

        index = persist_lineage_observation(
            db,
            organization_id=old_snapshot.organization_id,
            observation=LineageObservation(
                observation_kind="materialization",
                source_external_id="worker-production-source",
                snapshot_external_id="worker-production-snapshot-v1",
                asset_external_id="worker-production-index",
                asset_kind="index",
                revision_external_id="worker-production-index@v1",
                sequence=1,
                dependencies=(
                    LineageRevisionReference(
                        asset_external_id="worker-production-chunk",
                        revision_external_id="worker-production-chunk@v1",
                        dependency_type="indexed_from",
                    ),
                ),
            ),
        )

        observation_ids.append(index.record.id)

        persist_temporal_lineage_correlation(
            db,
            organization_id=old_snapshot.organization_id,
            page_snapshot_id=old_snapshot.id,
            lineage_observation_id=root.record.id,
        )

        event = _make_event(
            db,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
        )

        event.vector_analysis_status = (
            VECTOR_ANALYSIS_STATUS_COMPLETED
        )
        event.semantic_similarity = 0.0
        event.semantic_distance = 1.0
        event.semantic_magnitude = 0.5

        db.add(event)
        db.commit()
        db.refresh(event)

        event_id = event.id
        organization_id = event.organization_id

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            temporal_invalidation_limit=1,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=False,
            process_temporal_invalidation=True,
            process_risk_scoring=False,
        )

        worker._select_temporal_invalidation_event_ids = (
            lambda db: [str(event_id)]
        )

        first = worker.process_temporal_invalidation_events()

        assert first.selected == 1
        assert first.completed == 1
        assert first.failed == 0

        db.expire_all()

        persisted_event = db.get(
            SemanticDriftEvent,
            event_id,
        )

        assert persisted_event is not None
        assert (
            persisted_event.temporal_invalidation_status
            == TEMPORAL_INVALIDATION_STATUS_COMPLETED
        )
        assert persisted_event.temporal_invalidation_attempts == 1
        assert persisted_event.temporal_invalidation_error is None
        assert (
            persisted_event.temporal_invalidation_analyzed_at
            is not None
        )

        decisions = (
            db.query(LineageInvalidationDecisionRecord)
            .filter(
                LineageInvalidationDecisionRecord.organization_id
                == organization_id,
                LineageInvalidationDecisionRecord.root_asset_external_id
                == "worker-production-chunk",
                LineageInvalidationDecisionRecord.root_revision_external_id
                == "worker-production-chunk@v1",
            )
            .all()
        )

        assert len(decisions) == 1

        decision = decisions[0]

        assert (
            decision.target_asset_external_id
            == "worker-production-index"
        )
        assert (
            decision.target_revision_external_id
            == "worker-production-index@v1"
        )
        assert decision.disposition == "invalidate"
        assert decision.reason_code == "causal_dependency_changed"

        provenance = (
            db.query(
                TemporalLineageInvalidationDecisionProvenanceRecord
            )
            .filter(
                TemporalLineageInvalidationDecisionProvenanceRecord.organization_id
                == organization_id,
                TemporalLineageInvalidationDecisionProvenanceRecord.semantic_drift_event_id
                == event_id,
            )
            .all()
        )

        assert len(provenance) == 1
        assert provenance[0].decision_id == decision.id

        execution_intents = (
            db.query(LineageInvalidationExecutionIntentRecord)
            .filter(
                LineageInvalidationExecutionIntentRecord.organization_id
                == organization_id,
                LineageInvalidationExecutionIntentRecord.decision_id
                == decision.id,
            )
            .count()
        )

        assert execution_intents == 0

        from src.services.pipeline_effectiveness_evidence import (
            read_pipeline_event_evidence,
        )
        decision_evidence = read_pipeline_event_evidence(
            db, organization_id=organization_id, event_id=event_id
        )
        assert decision_evidence["gates"]["causal_decisions_observed"] is True
        assert decision_evidence["measurements"]["causal_decision_records"] == 1
        # A causal decision is not a physical deletion or authorization.
        print("P2D_CAUSAL_EVIDENCE=" + json.dumps(decision_evidence, sort_keys=True))

        replay = worker.process_temporal_invalidation_events()

        assert replay.selected == 1
        assert replay.completed == 0
        assert replay.failed == 0

        db.expire_all()

        replay_event = db.get(
            SemanticDriftEvent,
            event_id,
        )

        assert replay_event is not None
        assert replay_event.temporal_invalidation_attempts == 1

        assert (
            db.query(LineageInvalidationDecisionRecord)
            .filter(
                LineageInvalidationDecisionRecord.organization_id
                == organization_id,
                LineageInvalidationDecisionRecord.root_asset_external_id
                == "worker-production-chunk",
                LineageInvalidationDecisionRecord.root_revision_external_id
                == "worker-production-chunk@v1",
            )
            .count()
            == 1
        )

        assert (
            db.query(
                TemporalLineageInvalidationDecisionProvenanceRecord
            )
            .filter(
                TemporalLineageInvalidationDecisionProvenanceRecord.organization_id
                == organization_id,
                TemporalLineageInvalidationDecisionProvenanceRecord.semantic_drift_event_id
                == event_id,
            )
            .count()
            == 1
        )

    finally:
        db.rollback()

        if event is not None:
            db.query(
                TemporalLineageInvalidationDecisionProvenanceRecord
            ).filter(
                TemporalLineageInvalidationDecisionProvenanceRecord.semantic_drift_event_id
                == event.id
            ).delete(
                synchronize_session=False
            )

            db.query(
                LineageInvalidationDecisionRecord
            ).filter(
                LineageInvalidationDecisionRecord.organization_id
                == event.organization_id,
                LineageInvalidationDecisionRecord.root_asset_external_id
                == "worker-production-chunk",
                LineageInvalidationDecisionRecord.root_revision_external_id
                == "worker-production-chunk@v1",
            ).delete(
                synchronize_session=False
            )

            db.commit()

        if old_snapshot is not None:
            db.query(
                TemporalLineageCorrelationRecord
            ).filter(
                TemporalLineageCorrelationRecord.page_snapshot_id
                == old_snapshot.id
            ).delete(
                synchronize_session=False
            )

            db.commit()

        if observation_ids:
            db.query(
                LineageObservationRecord
            ).filter(
                LineageObservationRecord.id.in_(
                    observation_ids
                )
            ).delete(
                synchronize_session=False
            )

            db.commit()

        _delete_test_rows(
            db,
            event_ids=(
                [event.id]
                if event is not None
                else []
            ),
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )

        db.close()


def test_concurrent_temporal_invalidation_workers_complete_once(
    monkeypatch,
):
    db = SessionLocal()

    old_snapshot = None
    new_snapshot = None
    event = None

    try:
        existing = _existing_snapshot(db)

        old_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Concurrent temporal old.",
            embedding=_vector(0),
            embedding_status="completed",
        )

        new_snapshot = _make_snapshot(
            db,
            existing=existing,
            raw_content="Concurrent temporal new.",
            embedding=_vector(1),
            embedding_status="completed",
        )

        event = _make_event(
            db,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
        )

        event.vector_analysis_status = (
            VECTOR_ANALYSIS_STATUS_COMPLETED
        )
        event.semantic_similarity = 0.0
        event.semantic_distance = 1.0
        event.semantic_magnitude = 0.5

        db.add(event)
        db.commit()
        db.refresh(event)

        event_id = str(event.id)
        start_barrier = Barrier(2)
        analysis_calls: list[str] = []

        def analyze_once(
            db,
            *,
            organization_id,
            event_id,
            policy_identity,
        ):
            analysis_calls.append(str(event_id))

            class Result:
                roots = ()
                analyses = ()

            return Result()

        monkeypatch.setattr(
            vector_worker_module,
            "analyze_temporal_lineage_invalidation",
            analyze_once,
        )

        def run_worker():
            worker = VectorWorker(
                snapshot_limit=1,
                event_limit=1,
                temporal_invalidation_limit=1,
                max_attempts=3,
                process_embeddings=False,
                process_analysis=False,
                process_temporal_invalidation=True,
                process_risk_scoring=False,
            )

            worker._select_temporal_invalidation_event_ids = (
                lambda db: [event_id]
            )

            start_barrier.wait(timeout=10)

            return (
                worker.process_temporal_invalidation_events()
            )

        with ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            futures = [
                executor.submit(run_worker)
                for _ in range(2)
            ]

            results = [
                future.result(timeout=30)
                for future in futures
            ]

        assert sum(
            result.selected
            for result in results
        ) == 2

        assert sum(
            result.completed
            for result in results
        ) == 1

        assert sum(
            result.failed
            for result in results
        ) == 0

        assert len(analysis_calls) == 1

        db.expire_all()

        persisted = db.get(
            SemanticDriftEvent,
            event.id,
        )

        assert persisted is not None
        assert (
            persisted.temporal_invalidation_status
            == TEMPORAL_INVALIDATION_STATUS_COMPLETED
        )
        assert persisted.temporal_invalidation_attempts == 1
        assert persisted.temporal_invalidation_error is None
        assert (
            persisted.temporal_invalidation_analyzed_at
            is not None
        )

    finally:
        _delete_test_rows(
            db,
            event_ids=[
                event.id
                for event in (event,)
                if event is not None
            ],
            snapshot_ids=[
                snapshot.id
                for snapshot in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )
        db.close()
