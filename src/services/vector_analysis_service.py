"""
Vector analysis service for semantic drift events.

Responsibilities:
- Validate that both snapshots have usable completed embeddings.
- Calculate deterministic vector drift metrics.
- Persist vector-analysis lifecycle state safely.
- Keep vector analysis independent from drift-event creation.
- Avoid external embedding/API calls.

Batch orchestration and historical backfills intentionally belong
in a separate worker or script.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from src.models.page_snapshot import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_STATUS_COMPLETED,
    PageSnapshot,
)
from src.models.semantic_drift_event import (
    VECTOR_ANALYSIS_STATUS_COMPLETED,
    VECTOR_ANALYSIS_STATUS_FAILED,
    VECTOR_ANALYSIS_STATUS_PENDING,
    VECTOR_ANALYSIS_STATUS_PROCESSING,
    SemanticDriftEvent,
)
from src.services.embedding_service import (
    calculate_vector_metrics,
)


logger = logging.getLogger(__name__)


class VectorAnalysisError(RuntimeError):
    """Base error raised by the vector analysis service."""


class VectorAnalysisNotReadyError(VectorAnalysisError):
    """
    Raised when vector analysis cannot run yet.

    This is intentionally different from a failed analysis.
    For example, a snapshot whose embedding is still pending
    means the drift event should remain pending.
    """


class VectorAnalysisValidationError(VectorAnalysisError):
    """
    Raised when embeddings exist but are invalid or incompatible.
    """


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _embedding_values(
    embedding: Iterable[float] | None,
) -> list[float]:
    """
    Convert a pgvector/Python embedding into a validated list.
    """
    if embedding is None:
        raise VectorAnalysisNotReadyError(
            "Snapshot embedding is not available."
        )

    try:
        values = [
            float(value)
            for value in embedding
        ]
    except (TypeError, ValueError) as exc:
        raise VectorAnalysisValidationError(
            "Snapshot embedding contains invalid values."
        ) from exc

    if not values:
        raise VectorAnalysisValidationError(
            "Snapshot embedding is empty."
        )

    if not all(
        math.isfinite(value)
        for value in values
    ):
        raise VectorAnalysisValidationError(
            "Snapshot embedding contains non-finite values."
        )

    return values


def _validate_snapshot_embedding(
    snapshot: PageSnapshot,
) -> list[float]:
    """
    Validate one snapshot before vector analysis.

    Missing/incomplete embeddings are considered "not ready".
    Completed but malformed embeddings are considered failures.
    """
    if (
        snapshot.embedding_status
        != EMBEDDING_STATUS_COMPLETED
    ):
        raise VectorAnalysisNotReadyError(
            "Snapshot embedding is not completed: "
            f"snapshot={snapshot.id} "
            f"status={snapshot.embedding_status}"
        )

    if snapshot.embedding is None:
        raise VectorAnalysisValidationError(
            "Snapshot is marked embedding completed "
            "but has no embedding: "
            f"snapshot={snapshot.id}"
        )

    dimensions = snapshot.embedding_dimensions

    if dimensions is None:
        raise VectorAnalysisValidationError(
            "Snapshot embedding dimensions are missing: "
            f"snapshot={snapshot.id}"
        )

    if int(dimensions) != EMBEDDING_DIMENSIONS:
        raise VectorAnalysisValidationError(
            "Snapshot embedding dimension metadata mismatch: "
            f"snapshot={snapshot.id} "
            f"expected={EMBEDDING_DIMENSIONS} "
            f"actual={dimensions}"
        )

    if not snapshot.embedding_model:
        raise VectorAnalysisValidationError(
            "Snapshot embedding model is missing: "
            f"snapshot={snapshot.id}"
        )

    values = _embedding_values(
        snapshot.embedding
    )

    if len(values) != EMBEDDING_DIMENSIONS:
        raise VectorAnalysisValidationError(
            "Snapshot embedding vector dimension mismatch: "
            f"snapshot={snapshot.id} "
            f"expected={EMBEDDING_DIMENSIONS} "
            f"actual={len(values)}"
        )

    return values


def _validate_snapshot_pair(
    old_snapshot: PageSnapshot,
    new_snapshot: PageSnapshot,
) -> tuple[list[float], list[float]]:
    """
    Validate the two embeddings used by one drift event.
    """
    old_embedding = (
        _validate_snapshot_embedding(
            old_snapshot
        )
    )

    new_embedding = (
        _validate_snapshot_embedding(
            new_snapshot
        )
    )

    if (
        old_snapshot.embedding_model
        != new_snapshot.embedding_model
    ):
        raise VectorAnalysisValidationError(
            "Snapshot embedding models do not match: "
            f"old={old_snapshot.embedding_model} "
            f"new={new_snapshot.embedding_model}"
        )

    if (
        old_snapshot.embedding_dimensions
        != new_snapshot.embedding_dimensions
    ):
        raise VectorAnalysisValidationError(
            "Snapshot embedding dimensions do not match: "
            f"old={old_snapshot.embedding_dimensions} "
            f"new={new_snapshot.embedding_dimensions}"
        )

    return (
        old_embedding,
        new_embedding,
    )


class VectorAnalysisService:
    """
    Calculate and persist vector metrics for SemanticDriftEvent.

    This service never generates embeddings and never calls an
    external provider. Both snapshot embeddings must already exist.
    """

    def analyze_event(
        self,
        db: Session,
        event: SemanticDriftEvent,
    ) -> SemanticDriftEvent:
        """
        Analyze one semantic drift event.

        Lifecycle:

            pending
                -> processing
                -> completed

        Not-ready snapshot embeddings:

            pending
                -> pending

        Invalid embeddings or calculation/persistence errors:

            pending/processing
                -> failed

        A completed event is idempotent and returned unchanged.
        A processing event is also returned unchanged so another
        worker does not immediately duplicate the same work.
        """
        if (
            event.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_COMPLETED
        ):
            logger.debug(
                "Vector analysis already completed: event=%s",
                event.id,
            )
            return event

        if (
            event.vector_analysis_status
            == VECTOR_ANALYSIS_STATUS_PROCESSING
        ):
            logger.info(
                "Vector analysis already processing: event=%s",
                event.id,
            )
            return event

        old_snapshot = event.old_snapshot
        new_snapshot = event.new_snapshot

        if old_snapshot is None:
            return self._mark_failed(
                db,
                event,
                "Old snapshot relationship is unavailable.",
            )

        if new_snapshot is None:
            return self._mark_failed(
                db,
                event,
                "New snapshot relationship is unavailable.",
            )

        try:
            (
                old_embedding,
                new_embedding,
            ) = _validate_snapshot_pair(
                old_snapshot,
                new_snapshot,
            )

        except VectorAnalysisNotReadyError as exc:
            return self._mark_pending(
                db,
                event,
                str(exc),
            )

        except VectorAnalysisValidationError as exc:
            return self._mark_failed(
                db,
                event,
                str(exc),
            )

        try:
            event.vector_analysis_status = (
                VECTOR_ANALYSIS_STATUS_PROCESSING
            )

            event.vector_analysis_error = None

            event.vector_analysis_attempts = (
                int(
                    event.vector_analysis_attempts
                    or 0
                )
                + 1
            )

            db.add(
                event
            )

            db.commit()

            db.refresh(
                event
            )

            metrics = calculate_vector_metrics(
                old_embedding,
                new_embedding,
            )

            semantic_similarity = float(
                metrics["semantic_similarity"]
            )

            semantic_distance = float(
                metrics["semantic_distance"]
            )

            semantic_magnitude = float(
                metrics["semantic_magnitude"]
            )

            if not all(
                math.isfinite(value)
                for value in (
                    semantic_similarity,
                    semantic_distance,
                    semantic_magnitude,
                )
            ):
                raise VectorAnalysisValidationError(
                    "Vector analysis produced non-finite metrics."
                )

            if not (
                -1.0
                <= semantic_similarity
                <= 1.0
            ):
                raise VectorAnalysisValidationError(
                    "semantic_similarity is outside [-1, 1]."
                )

            if not (
                0.0
                <= semantic_distance
                <= 2.0
            ):
                raise VectorAnalysisValidationError(
                    "semantic_distance is outside [0, 2]."
                )

            if not (
                0.0
                <= semantic_magnitude
                <= 1.0
            ):
                raise VectorAnalysisValidationError(
                    "semantic_magnitude is outside [0, 1]."
                )

            event.semantic_similarity = (
                semantic_similarity
            )

            event.semantic_distance = (
                semantic_distance
            )

            event.semantic_magnitude = (
                semantic_magnitude
            )

            event.vector_analysis_status = (
                VECTOR_ANALYSIS_STATUS_COMPLETED
            )

            event.vector_analysis_error = None

            event.vector_analyzed_at = utc_now()

            db.add(
                event
            )

            db.commit()

            db.refresh(
                event
            )

            logger.info(
                "Vector analysis completed: "
                "event=%s similarity=%.6f "
                "distance=%.6f magnitude=%.6f",
                event.id,
                semantic_similarity,
                semantic_distance,
                semantic_magnitude,
            )

            return event

        except Exception as exc:
            db.rollback()

            logger.exception(
                "Vector analysis failed: event=%s",
                event.id,
            )

            return self._mark_failed(
                db,
                event,
                str(exc),
            )

    def _mark_pending(
        self,
        db: Session,
        event: SemanticDriftEvent,
        reason: str,
    ) -> SemanticDriftEvent:
        """
        Keep an event pending when required embeddings are not ready.

        Missing embeddings are expected during asynchronous backfills,
        so they do not consume a vector-analysis attempt.
        """
        try:
            event.vector_analysis_status = (
                VECTOR_ANALYSIS_STATUS_PENDING
            )

            event.vector_analysis_error = (
                reason[:2000]
            )

            event.semantic_similarity = None
            event.semantic_distance = None
            event.semantic_magnitude = None
            event.vector_analyzed_at = None

            db.add(
                event
            )

            db.commit()

            db.refresh(
                event
            )

            logger.info(
                "Vector analysis remains pending: "
                "event=%s reason=%s",
                event.id,
                reason,
            )

            return event

        except Exception:
            db.rollback()

            logger.exception(
                "Unable to persist pending vector-analysis "
                "state: event=%s",
                event.id,
            )

            raise

    def _mark_failed(
        self,
        db: Session,
        event: SemanticDriftEvent,
        reason: str,
    ) -> SemanticDriftEvent:
        """
        Persist a failed vector-analysis state.

        This never deletes or rolls back the already-persisted
        SemanticDriftEvent itself.
        """
        try:
            event.vector_analysis_status = (
                VECTOR_ANALYSIS_STATUS_FAILED
            )

            event.vector_analysis_error = (
                reason[:2000]
            )

            event.semantic_similarity = None
            event.semantic_distance = None
            event.semantic_magnitude = None
            event.vector_analyzed_at = None

            db.add(
                event
            )

            db.commit()

            db.refresh(
                event
            )

            logger.error(
                "Vector analysis marked failed: "
                "event=%s reason=%s",
                event.id,
                reason,
            )

            return event

        except Exception:
            db.rollback()

            logger.exception(
                "Unable to persist failed vector-analysis "
                "state: event=%s",
                event.id,
            )

            raise