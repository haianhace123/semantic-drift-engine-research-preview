from __future__ import annotations

import os
import uuid

import pytest

from src.core.database import SessionLocal
from src.models.page_snapshot import PageSnapshot
from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.risk_scoring_service import (
    RiskScoringService,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="Database integration tests are disabled.",
)


def _build_event(
    db,
    *,
    vector_analysis_status: str,
    semantic_magnitude: float | None,
    impact_score: float = 80.0,
    risk_level: str = "high",
    confidence: float = 0.9,
) -> SemanticDriftEvent:
    """
    Create one disposable semantic drift event using ownership
    information from an existing PageSnapshot.

    The risk scoring service commits internally, so integration tests
    explicitly delete disposable rows during cleanup.
    """
    snapshot = (
        db.query(PageSnapshot)
        .order_by(PageSnapshot.crawled_at.asc())
        .first()
    )

    if snapshot is None:
        pytest.skip(
            "No PageSnapshot rows are available for integration testing."
        )

    event = SemanticDriftEvent(
        organization_id=snapshot.organization_id,
        monitored_page_id=snapshot.monitored_page_id,
        old_snapshot_id=snapshot.id,
        new_snapshot_id=snapshot.id,
        semantic_delta_json={
            "source": "risk_scoring_integration_test",
            "changes": [],
        },
        change_type="integration_test",
        risk_level=risk_level,
        impact_score=impact_score,
        confidence=confidence,
        semantic_magnitude=semantic_magnitude,
        vector_analysis_status=vector_analysis_status,
        risk_scoring_status="pending",
        risk_scoring_attempts=0,
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    return event


def _delete_event(
    db,
    event_id,
) -> None:
    db.rollback()

    event = (
        db.query(SemanticDriftEvent)
        .filter(
            SemanticDriftEvent.id == event_id
        )
        .first()
    )

    if event is not None:
        db.delete(event)
        db.commit()


def test_risk_scoring_remains_pending_without_vector_evidence():
    db = SessionLocal()
    event_id = None

    try:
        event = _build_event(
            db,
            vector_analysis_status="pending",
            semantic_magnitude=None,
        )

        event_id = event.id

        result = RiskScoringService().score_event(
            db,
            event,
        )

        assert result.risk_scoring_status == "pending"
        assert result.risk_scoring_attempts == 0

        assert result.risk_score is None
        assert result.risk_score_version is None
        assert result.risk_score_components is None
        assert result.risk_scored_at is None

        assert result.risk_scoring_error is not None
        assert (
            "Vector analysis is not completed"
            in result.risk_scoring_error
        )

        db.expire_all()

        persisted = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id == event_id
            )
            .one()
        )

        assert persisted.risk_scoring_status == "pending"
        assert persisted.risk_scoring_attempts == 0
        assert persisted.risk_score is None

        assert persisted.risk_scoring_error is not None

    finally:
        if event_id is not None:
            _delete_event(
                db,
                event_id,
            )

        db.close()


def test_risk_scoring_completes_and_persists():
    db = SessionLocal()
    event_id = None

    try:
        event = _build_event(
            db,
            vector_analysis_status="completed",
            semantic_magnitude=0.8,
            impact_score=80.0,
            risk_level="high",
            confidence=0.9,
        )

        event_id = event.id

        result = RiskScoringService().score_event(
            db,
            event,
        )

        assert result.risk_scoring_status == "completed"
        assert result.risk_scoring_attempts == 1

        assert result.risk_score is not None
        assert 0.0 <= result.risk_score <= 100.0

        assert result.risk_score_version == "v1"

        assert result.risk_score_components is not None
        assert isinstance(
            result.risk_score_components,
            dict,
        )

        components = result.risk_score_components

        assert "semantic_magnitude" in components
        assert "impact" in components
        assert "severity" in components
        assert "context" in components
        assert "confidence" in components

        assert (
            components["semantic_magnitude"]["value"]
            == pytest.approx(0.8)
        )

        assert (
            components["impact"]["value"]
            == pytest.approx(80.0)
        )

        assert result.risk_scoring_error is None
        assert result.risk_scored_at is not None

        db.expire_all()

        persisted = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id == event_id
            )
            .one()
        )

        assert persisted.risk_scoring_status == "completed"
        assert persisted.risk_scoring_attempts == 1

        assert persisted.risk_score == pytest.approx(
            result.risk_score
        )

        assert persisted.risk_score_version == "v1"

        assert persisted.risk_score_components is not None

        assert (
            persisted.risk_score_components["semantic_magnitude"][
                "value"
            ]
            == pytest.approx(0.8)
        )

        assert persisted.risk_scoring_error is None
        assert persisted.risk_scored_at is not None

    finally:
        if event_id is not None:
            _delete_event(
                db,
                event_id,
            )

        db.close()


def test_completed_risk_score_is_idempotent():
    db = SessionLocal()
    event_id = None

    try:
        event = _build_event(
            db,
            vector_analysis_status="completed",
            semantic_magnitude=0.42,
            impact_score=55.0,
            risk_level="medium",
            confidence=0.73,
        )

        event_id = event.id

        service = RiskScoringService()

        first = service.score_event(
            db,
            event,
        )

        first_score = first.risk_score
        first_version = first.risk_score_version
        first_components = dict(
            first.risk_score_components
        )
        first_attempts = first.risk_scoring_attempts
        first_scored_at = first.risk_scored_at

        second = service.score_event(
            db,
            first,
        )

        assert second.risk_scoring_status == "completed"

        assert second.risk_score == pytest.approx(
            first_score
        )

        assert (
            second.risk_score_version
            == first_version
        )

        assert (
            second.risk_score_components
            == first_components
        )

        assert (
            second.risk_scoring_attempts
            == first_attempts
            == 1
        )

        assert (
            second.risk_scored_at
            == first_scored_at
        )

        db.expire_all()

        persisted = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id == event_id
            )
            .one()
        )

        assert persisted.risk_scoring_attempts == 1
        assert persisted.risk_score == pytest.approx(
            first_score
        )

        assert (
            persisted.risk_scored_at
            == first_scored_at
        )

    finally:
        if event_id is not None:
            _delete_event(
                db,
                event_id,
            )

        db.close()


def test_invalid_risk_input_marks_event_failed():
    db = SessionLocal()
    event_id = None

    try:
        event = _build_event(
            db,
            vector_analysis_status="completed",
            semantic_magnitude=0.5,
            impact_score=150.0,
            risk_level="high",
            confidence=0.9,
        )

        event_id = event.id

        with pytest.raises(Exception):
            RiskScoringService().score_event(
                db,
                event,
            )

        db.expire_all()

        persisted = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id == event_id
            )
            .one()
        )

        assert persisted.risk_scoring_status == "failed"
        assert persisted.risk_scoring_attempts == 1

        assert persisted.risk_score is None
        assert persisted.risk_score_version is None
        assert persisted.risk_score_components is None
        assert persisted.risk_scored_at is None

        assert persisted.risk_scoring_error is not None

    finally:
        if event_id is not None:
            _delete_event(
                db,
                event_id,
            )

        db.close()