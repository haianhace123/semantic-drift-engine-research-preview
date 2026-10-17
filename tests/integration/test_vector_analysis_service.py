from __future__ import annotations

import hashlib
import os
import uuid

import pytest

from src.core.database import SessionLocal
from src.models.page_snapshot import (
    EMBEDDING_DIMENSIONS,
    PageSnapshot,
)
from src.models.semantic_drift_event import (
    VECTOR_ANALYSIS_STATUS_COMPLETED,
    VECTOR_ANALYSIS_STATUS_FAILED,
    VECTOR_ANALYSIS_STATUS_PENDING,
    SemanticDriftEvent,
)
from src.services.vector_analysis_service import (
    VectorAnalysisService,
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
    "integration-test-embedding-model"
)


def _vector(
    index: int,
) -> list[float]:
    """
    Create a deterministic non-zero 1536-dimensional vector.
    """
    values = [
        0.0
        for _ in range(
            EMBEDDING_DIMENSIONS
        )
    ]

    values[index] = 1.0

    return values


def _make_snapshot(
    db,
    *,
    existing: PageSnapshot,
    raw_content: str,
    embedding: list[float] | None,
    embedding_status: str,
    embedding_model: str | None = (
        TEST_EMBEDDING_MODEL
    ),
    embedding_dimensions: int | None = (
        EMBEDDING_DIMENSIONS
    ),
) -> PageSnapshot:
    """
    Create a disposable snapshot using ownership from an
    existing database snapshot.
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
        embedding_model=embedding_model,
        embedding_dimensions=(
            embedding_dimensions
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
) -> SemanticDriftEvent:
    """
    Create a disposable drift event for two test snapshots.
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
        },
        summary_text=(
            "Vector analysis integration test."
        ),
        analysis_status="completed",
        risk_level="unknown",
        vector_analysis_status=(
            VECTOR_ANALYSIS_STATUS_PENDING
        ),
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
    event_id=None,
    snapshot_ids=None,
) -> None:
    """
    Delete disposable rows even when the service commits internally.
    """
    db.rollback()

    if event_id is not None:
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


def _existing_snapshot(
    db,
) -> PageSnapshot:
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


def test_vector_analysis_completes_and_persists_metrics():
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
                    "Vector analysis old content."
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
                    "Vector analysis new content."
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
            )
        )

        service = (
            VectorAnalysisService()
        )

        result = (
            service.analyze_event(
                db,
                event,
            )
        )

        assert (
            result.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_COMPLETED
        )

        assert (
            result.vector_analysis_error
            is None
        )

        assert (
            result.vector_analysis_attempts
            == 1
        )

        assert (
            result.vector_analyzed_at
            is not None
        )

        assert (
            result.semantic_similarity
            == pytest.approx(
                0.0,
                abs=1e-7,
            )
        )

        assert (
            result.semantic_distance
            == pytest.approx(
                1.0,
                abs=1e-7,
            )
        )

        assert (
            result.semantic_magnitude
            == pytest.approx(
                0.5,
                abs=1e-7,
            )
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

    finally:
        _delete_test_rows(
            db,
            event_id=(
                event.id
                if event is not None
                else None
            ),
            snapshot_ids=[
                snapshot.id
                for snapshot
                in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )

        db.close()


def test_vector_analysis_remains_pending_when_embedding_not_ready():
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
                    "Pending test old content."
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
                    "Pending test new content."
                ),
                embedding=None,
                embedding_status="pending",
                embedding_model=None,
                embedding_dimensions=None,
            )
        )

        event = (
            _make_event(
                db,
                old_snapshot=old_snapshot,
                new_snapshot=new_snapshot,
            )
        )

        service = (
            VectorAnalysisService()
        )

        result = (
            service.analyze_event(
                db,
                event,
            )
        )

        assert (
            result.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_PENDING
        )

        assert (
            result.vector_analysis_error
            is not None
        )

        assert (
            "not completed"
            in result.vector_analysis_error
        )

        assert (
            result.vector_analysis_attempts
            == 0
        )

        assert (
            result.semantic_similarity
            is None
        )

        assert (
            result.semantic_distance
            is None
        )

        assert (
            result.semantic_magnitude
            is None
        )

        assert (
            result.vector_analyzed_at
            is None
        )

    finally:
        _delete_test_rows(
            db,
            event_id=(
                event.id
                if event is not None
                else None
            ),
            snapshot_ids=[
                snapshot.id
                for snapshot
                in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )

        db.close()


def test_vector_analysis_marks_incompatible_models_failed():
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
                    "Failed test old content."
                ),
                embedding=_vector(
                    0
                ),
                embedding_status="completed",
                embedding_model="model-a",
            )
        )

        new_snapshot = (
            _make_snapshot(
                db,
                existing=existing,
                raw_content=(
                    "Failed test new content."
                ),
                embedding=_vector(
                    1
                ),
                embedding_status="completed",
                embedding_model="model-b",
            )
        )

        event = (
            _make_event(
                db,
                old_snapshot=old_snapshot,
                new_snapshot=new_snapshot,
            )
        )

        service = (
            VectorAnalysisService()
        )

        result = (
            service.analyze_event(
                db,
                event,
            )
        )

        assert (
            result.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_FAILED
        )

        assert (
            result.vector_analysis_error
            is not None
        )

        assert (
            "models do not match"
            in result.vector_analysis_error
        )

        assert (
            result.vector_analysis_attempts
            == 0
        )

        assert (
            result.semantic_similarity
            is None
        )

        assert (
            result.semantic_distance
            is None
        )

        assert (
            result.semantic_magnitude
            is None
        )

        assert (
            result.vector_analyzed_at
            is None
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
            == VECTOR_ANALYSIS_STATUS_FAILED
        )

        assert (
            persisted.vector_analysis_error
            is not None
        )

    finally:
        _delete_test_rows(
            db,
            event_id=(
                event.id
                if event is not None
                else None
            ),
            snapshot_ids=[
                snapshot.id
                for snapshot
                in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )

        db.close()


def test_vector_analysis_completed_event_is_idempotent():
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
                    "Idempotency old content."
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
                    "Idempotency new content."
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
            )
        )

        service = (
            VectorAnalysisService()
        )

        first_result = (
            service.analyze_event(
                db,
                event,
            )
        )

        assert (
            first_result.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_COMPLETED
        )

        first_attempts = (
            first_result.vector_analysis_attempts
        )

        first_similarity = (
            first_result.semantic_similarity
        )

        first_distance = (
            first_result.semantic_distance
        )

        first_magnitude = (
            first_result.semantic_magnitude
        )

        first_analyzed_at = (
            first_result.vector_analyzed_at
        )

        second_result = (
            service.analyze_event(
                db,
                first_result,
            )
        )

        assert (
            second_result.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_COMPLETED
        )

        assert (
            second_result.vector_analysis_attempts
            == first_attempts
        )

        assert (
            second_result.semantic_similarity
            == first_similarity
        )

        assert (
            second_result.semantic_distance
            == first_distance
        )

        assert (
            second_result.semantic_magnitude
            == first_magnitude
        )

        assert (
            second_result.vector_analyzed_at
            == first_analyzed_at
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
            persisted.vector_analysis_attempts
            == 1
        )

    finally:
        _delete_test_rows(
            db,
            event_id=(
                event.id
                if event is not None
                else None
            ),
            snapshot_ids=[
                snapshot.id
                for snapshot
                in (
                    old_snapshot,
                    new_snapshot,
                )
                if snapshot is not None
            ],
        )

        db.close()