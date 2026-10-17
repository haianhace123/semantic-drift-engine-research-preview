from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from src.services.embedding_service import (
    EmbeddingService,
    calculate_vector_metrics,
)


class TextEmbeddingService(Protocol):
    provider: str
    model: str
    dimensions: int

    def generate_embedding(
        self,
        text: str,
    ) -> list[float]:
        ...


@dataclass(frozen=True)
class EmbeddingBenchmarkMetadata:
    provider: str
    model: str
    dimensions: int


@dataclass(frozen=True)
class EmbeddingPairScore:
    semantic_similarity: float
    semantic_distance: float
    semantic_magnitude: float


class ProductionEmbeddingAdapter:
    def __init__(
        self,
        service: TextEmbeddingService | None = None,
    ) -> None:
        self._service = (
            service
            if service is not None
            else EmbeddingService()
        )

    @property
    def metadata(
        self,
    ) -> EmbeddingBenchmarkMetadata:
        return EmbeddingBenchmarkMetadata(
            provider=self._service.provider,
            model=self._service.model,
            dimensions=self._service.dimensions,
        )

    def embed_text(
        self,
        text: str,
    ) -> tuple[float, ...]:
        return tuple(
            self._service.generate_embedding(
                text
            )
        )

    def score_embeddings(
        self,
        before_embedding: Sequence[float],
        after_embedding: Sequence[float],
    ) -> EmbeddingPairScore:
        metrics = calculate_vector_metrics(
            before_embedding,
            after_embedding,
        )

        return EmbeddingPairScore(
            semantic_similarity=float(
                metrics["semantic_similarity"]
            ),
            semantic_distance=float(
                metrics["semantic_distance"]
            ),
            semantic_magnitude=float(
                metrics["semantic_magnitude"]
            ),
        )

    def score_pair(
        self,
        before: str,
        after: str,
    ) -> EmbeddingPairScore:
        before_embedding = self.embed_text(
            before
        )
        after_embedding = self.embed_text(
            after
        )

        return self.score_embeddings(
            before_embedding,
            after_embedding,
        )