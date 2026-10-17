from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from src.core.database import SessionLocal
from src.models.page_snapshot import PageSnapshot
from src.services.embedding_service import (
    EMBEDDING_DIMENSIONS,
    EmbeddingResponseError,
    EmbeddingService,
    calculate_vector_metrics,
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


@pytest.fixture(autouse=True)
def use_openai_embedding_provider(
    monkeypatch,
):
    """
    Preserve the historical OpenAI integration-test baseline regardless
    of the embedding provider selected in the developer's local .env.

    Gemini-specific coverage lives in provider-specific tests.
    """
    monkeypatch.setattr(
        "src.services.embedding_service.settings.EMBEDDING_PROVIDER",
        "openai",
    )


class FakeEmbeddingsResource:
    def __init__(
        self,
        embedding: list[float],
    ) -> None:
        self.embedding = embedding
        self.calls: list[dict] = []

    def create(
        self,
        **kwargs,
    ):
        self.calls.append(
            dict(kwargs)
        )

        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    embedding=list(
                        self.embedding
                    )
                )
            ]
        )


class FakeOpenAIClient:
    def __init__(
        self,
        embedding: list[float],
    ) -> None:
        self.embeddings = (
            FakeEmbeddingsResource(
                embedding
            )
        )


def _make_test_snapshot(
    db,
    *,
    raw_content: str,
) -> PageSnapshot:
    """
    Create a disposable snapshot using the tenant/page ownership of an
    existing snapshot.

    The embedding service commits internally, so this test cannot rely
    on transaction rollback for isolation. Every created row is deleted
    explicitly in the test finally block.
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

    content_hash = hashlib.sha256(
        raw_content.encode(
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
    )

    db.add(
        snapshot
    )

    db.commit()

    db.refresh(
        snapshot
    )

    return snapshot


def _delete_test_snapshot(
    db,
    snapshot_id,
) -> None:
    """
    Remove a disposable snapshot even when the service committed state
    changes during the test.
    """
    db.rollback()

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


def test_embedding_service_persists_vector_successfully():
    db = SessionLocal()

    snapshot = None

    try:
        snapshot = (
            _make_test_snapshot(
                db,
                raw_content=(
                    "Semantic Drift Engine "
                    "offline embedding "
                    "integration test."
                ),
            )
        )

        fake_vector = [
            0.0
            for _ in range(
                EMBEDDING_DIMENSIONS
            )
        ]

        # Make the vector non-zero so cosine operations remain valid.
        fake_vector[0] = 1.0
        fake_vector[1] = 0.25
        fake_vector[2] = -0.125

        fake_client = (
            FakeOpenAIClient(
                fake_vector
            )
        )

        service = (
            EmbeddingService(
                client=fake_client
            )
        )

        result = (
            service.embed_snapshot(
                db,
                snapshot,
            )
        )

        assert (
            result.embedding_status
            == "completed"
        )

        assert (
            result.embedding_model
            == service.model
        )

        assert (
            result.embedding_dimensions
            == EMBEDDING_DIMENSIONS
        )

        assert (
            result.embedding_attempts
            == 1
        )

        assert (
            result.embedding_error
            is None
        )

        assert (
            result.embedded_at
            is not None
        )

        assert (
            result.embedding
            is not None
        )

        assert (
            len(
                result.embedding
            )
            == EMBEDDING_DIMENSIONS
        )

        # Verify provider contract.
        assert (
            len(
                fake_client
                .embeddings
                .calls
            )
            == 1
        )

        call = (
            fake_client
            .embeddings
            .calls[0]
        )

        assert (
            call["model"]
            == service.model
        )

        assert (
            call["dimensions"]
            == EMBEDDING_DIMENSIONS
        )

        assert (
            call["encoding_format"]
            == "float"
        )

        assert (
            "Semantic Drift Engine"
            in call["input"]
        )

        # Expire ORM state so the following checks reload values from DB.
        db.expire_all()

        persisted = (
            db.query(
                PageSnapshot
            )
            .filter(
                PageSnapshot.id
                == snapshot.id
            )
            .one()
        )

        assert (
            persisted.embedding_status
            == "completed"
        )

        assert (
            persisted.embedding_model
            == service.model
        )

        assert (
            persisted.embedding_dimensions
            == EMBEDDING_DIMENSIONS
        )

        assert (
            persisted.embedding_attempts
            == 1
        )

        assert (
            persisted.embedding_error
            is None
        )

        assert (
            persisted.embedding
            is not None
        )

        assert (
            len(
                persisted.embedding
            )
            == EMBEDDING_DIMENSIONS
        )

        # Verify pgvector itself sees exactly 1536 stored dimensions.
        stored_dimensions = (
            db.execute(
                text(
                    """
                    SELECT vector_dims(embedding)
                    FROM page_snapshots
                    WHERE id = :snapshot_id
                    """
                ),
                {
                    "snapshot_id":
                        str(
                            snapshot.id
                        ),
                },
            )
            .scalar_one()
        )

        assert (
            stored_dimensions
            == EMBEDDING_DIMENSIONS
        )

        # Verify deterministic vector metrics on reloaded DB data.
        metrics = (
            calculate_vector_metrics(
                persisted.embedding,
                fake_vector,
            )
        )

        assert metrics[
            "semantic_similarity"
        ] == pytest.approx(
            1.0,
            abs=1e-7,
        )

        assert metrics[
            "semantic_distance"
        ] == pytest.approx(
            0.0,
            abs=1e-7,
        )

        assert metrics[
            "semantic_magnitude"
        ] == pytest.approx(
            0.0,
            abs=1e-7,
        )

    finally:
        if snapshot is not None:
            _delete_test_snapshot(
                db,
                snapshot.id,
            )

        db.close()


def test_embedding_service_rejects_wrong_dimensions_and_marks_failed():
    db = SessionLocal()

    snapshot = None

    try:
        snapshot = (
            _make_test_snapshot(
                db,
                raw_content=(
                    "Invalid vector dimension "
                    "integration test."
                ),
            )
        )

        bad_vector = [
            0.01
            for _ in range(
                EMBEDDING_DIMENSIONS
                - 1
            )
        ]

        service = (
            EmbeddingService(
                client=(
                    FakeOpenAIClient(
                        bad_vector
                    )
                )
            )
        )

        with pytest.raises(
            EmbeddingResponseError,
            match=(
                "unexpected dimensions"
            ),
        ):
            service.embed_snapshot(
                db,
                snapshot,
            )

        db.expire_all()

        persisted = (
            db.query(
                PageSnapshot
            )
            .filter(
                PageSnapshot.id
                == snapshot.id
            )
            .one()
        )

        assert (
            persisted.embedding_status
            == "failed"
        )

        assert (
            persisted.embedding
            is None
        )

        assert (
            persisted.embedding_model
            is None
        )

        assert (
            persisted.embedding_dimensions
            is None
        )

        assert (
            persisted.embedded_at
            is None
        )

        assert (
            persisted.embedding_attempts
            == 1
        )

        assert (
            persisted.embedding_error
            is not None
        )

        assert (
            "unexpected dimensions"
            in persisted.embedding_error
        )

    finally:
        if snapshot is not None:
            _delete_test_snapshot(
                db,
                snapshot.id,
            )

        db.close()