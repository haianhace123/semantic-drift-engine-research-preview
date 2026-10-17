from __future__ import annotations

import pytest

from benchmarks.temporal_knowledge_integrity.embedding_adapter import (
    ProductionEmbeddingAdapter,
)


class FakeEmbeddingService:
    provider = "test"
    model = "test-embedding"
    dimensions = 2

    def __init__(
        self,
        embeddings: dict[str, list[float]],
    ) -> None:
        self._embeddings = embeddings
        self.calls: list[str] = []

    def generate_embedding(
        self,
        text: str,
    ) -> list[float]:
        self.calls.append(
            text
        )

        return list(
            self._embeddings[text]
        )


def test_adapter_exposes_embedding_metadata() -> None:
    service = FakeEmbeddingService(
        {}
    )
    adapter = ProductionEmbeddingAdapter(
        service=service
    )

    metadata = adapter.metadata

    assert metadata.provider == "test"
    assert metadata.model == "test-embedding"
    assert metadata.dimensions == 2


def test_adapter_embeds_text_through_service() -> None:
    service = FakeEmbeddingService(
        {
            "knowledge": [
                1.0,
                0.0,
            ],
        }
    )
    adapter = ProductionEmbeddingAdapter(
        service=service
    )

    embedding = adapter.embed_text(
        "knowledge"
    )

    assert embedding == (
        1.0,
        0.0,
    )
    assert service.calls == [
        "knowledge",
    ]


def test_adapter_scores_identical_embeddings_with_production_metrics() -> None:
    service = FakeEmbeddingService(
        {}
    )
    adapter = ProductionEmbeddingAdapter(
        service=service
    )

    result = adapter.score_embeddings(
        (
            1.0,
            0.0,
        ),
        (
            1.0,
            0.0,
        ),
    )

    assert result.semantic_similarity == pytest.approx(
        1.0
    )
    assert result.semantic_distance == pytest.approx(
        0.0
    )
    assert result.semantic_magnitude == pytest.approx(
        0.0
    )


def test_adapter_scores_opposite_embeddings_with_production_metrics() -> None:
    service = FakeEmbeddingService(
        {}
    )
    adapter = ProductionEmbeddingAdapter(
        service=service
    )

    result = adapter.score_embeddings(
        (
            1.0,
            0.0,
        ),
        (
            -1.0,
            0.0,
        ),
    )

    assert result.semantic_similarity == pytest.approx(
        -1.0
    )
    assert result.semantic_distance == pytest.approx(
        2.0
    )
    assert result.semantic_magnitude == pytest.approx(
        1.0
    )


def test_adapter_scores_pair_using_two_provider_calls() -> None:
    service = FakeEmbeddingService(
        {
            "before": [
                1.0,
                0.0,
            ],
            "after": [
                0.0,
                1.0,
            ],
        }
    )
    adapter = ProductionEmbeddingAdapter(
        service=service
    )

    result = adapter.score_pair(
        "before",
        "after",
    )

    assert service.calls == [
        "before",
        "after",
    ]
    assert result.semantic_similarity == pytest.approx(
        0.0
    )
    assert result.semantic_distance == pytest.approx(
        1.0
    )
    assert result.semantic_magnitude == pytest.approx(
        0.5
    )