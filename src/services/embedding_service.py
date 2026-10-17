"""
Embedding service for the vector semantic layer.

Responsibilities:
- Resolve snapshot text from raw_content or raw_content_path.
- Generate a fixed-size OpenAI embedding.
- Persist embedding lifecycle state safely.
- Calculate deterministic cosine metrics between embeddings.

This module intentionally does not perform batch backfills.
Batch orchestration belongs in a separate worker/script.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone
from typing import Iterable

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from google import genai
from google.genai import types as genai_types
from google.genai.errors import (
    ClientError as GeminiClientError,
    ServerError as GeminiServerError,
)
from sqlalchemy.orm import Session

from src.core.config import settings
from src.models.page_snapshot import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_STATUS_COMPLETED,
    EMBEDDING_STATUS_FAILED,
    EMBEDDING_STATUS_PROCESSING,
    PageSnapshot,
)


logger = logging.getLogger(__name__)


class EmbeddingServiceError(RuntimeError):
    """Base error raised by the embedding service."""


class SnapshotContentError(EmbeddingServiceError):
    """Raised when usable snapshot content cannot be resolved."""


class EmbeddingConfigurationError(EmbeddingServiceError):
    """Raised when embedding configuration is invalid."""


class EmbeddingResponseError(EmbeddingServiceError):
    """Raised when the provider returns an invalid embedding."""


class EmbeddingProviderUnavailableError(
    EmbeddingServiceError
):
    """
    Raised when the embedding provider has a systemic failure.

    The worker may use this signal to stop the current embedding
    batch so that one provider outage does not consume the retry
    budget of every queued snapshot.
    """


def _openai_error_code(
    exc: Exception,
) -> str:
    """
    Extract a normalized OpenAI provider error code.

    OpenAI SDK errors may expose the code directly or keep it
    inside a nested response body such as:

        {
            "error": {
                "code": "insufficient_quota"
            }
        }
    """
    direct_code = getattr(
        exc,
        "code",
        None,
    )

    if direct_code:
        return str(
            direct_code
        ).strip().lower()

    body = getattr(
        exc,
        "body",
        None,
    )

    if isinstance(
        body,
        dict,
    ):
        code = body.get(
            "code"
        )

        if code:
            return str(
                code
            ).strip().lower()

        nested_error = body.get(
            "error"
        )

        if isinstance(
            nested_error,
            dict,
        ):
            nested_code = nested_error.get(
                "code"
            )

            if nested_code:
                return str(
                    nested_code
                ).strip().lower()

    return ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_text(value: str) -> str:
    """
    Normalize snapshot text without changing its semantic structure.
    """
    if not value:
        return ""

    normalized = value.replace(
        "\x00",
        "",
    )

    return normalized.strip()


def load_snapshot_content(
    snapshot: PageSnapshot,
) -> str:
    """
    Resolve content for a PageSnapshot.

    raw_content is preferred when populated. Current crawler persistence
    normally stores raw_content=None and uses raw_content_path instead.
    """
    inline_content = _clean_text(
        snapshot.raw_content or ""
    )

    if inline_content:
        return inline_content

    path = snapshot.raw_content_path

    if not path:
        raise SnapshotContentError(
            f"Snapshot {snapshot.id} has no raw content or raw content path."
        )

    resolved_path = os.path.abspath(
        path
    )

    if not os.path.isfile(
        resolved_path
    ):
        raise SnapshotContentError(
            "Snapshot content file does not exist: "
            f"snapshot={snapshot.id} "
            f"path={resolved_path}"
        )

    try:
        with open(
            resolved_path,
            "r",
            encoding="utf-8",
            errors="replace",
        ) as file:
            content = _clean_text(
                file.read()
            )
    except OSError as exc:
        raise SnapshotContentError(
            "Unable to read snapshot content: "
            f"snapshot={snapshot.id} "
            f"path={resolved_path}"
        ) from exc

    if not content:
        raise SnapshotContentError(
            f"Snapshot {snapshot.id} content is empty."
        )

    return content


def normalize_embedding_text(
    content: str,
) -> str:
    """
    Apply the application-level input cap before calling the provider.
    """
    normalized = _clean_text(
        content
    )

    if not normalized:
        raise SnapshotContentError(
            "Embedding input is empty."
        )

    max_chars = int(
        settings.OPENAI_EMBEDDING_MAX_CHARS
    )

    if max_chars <= 0:
        raise EmbeddingConfigurationError(
            "OPENAI_EMBEDDING_MAX_CHARS must be greater than zero."
        )

    if len(normalized) > max_chars:
        normalized = normalized[
            :max_chars
        ]

    return normalized


class EmbeddingService:
    """
    OpenAI-backed fixed-dimension embedding service.
    """

    def __init__(
        self,
        client: OpenAI | None = None,
    ) -> None:
        self.provider = (
            settings.EMBEDDING_PROVIDER
            .strip()
            .lower()
        )

        if self.provider not in {
            "openai",
            "gemini",
        }:
            raise EmbeddingConfigurationError(
                "Unsupported embedding provider: "
                f"{self.provider!r}. "
                "Expected 'openai' or 'gemini'."
            )
        
        if self.provider == "gemini":
            self.model = (
                settings.GEMINI_EMBEDDING_MODEL
                .strip()
            )
        else:
            self.model = (
                settings.OPENAI_EMBEDDING_MODEL
                .strip()
            )

        self.dimensions = int(
            settings.OPENAI_EMBEDDING_DIMENSIONS
        )

        self.timeout_seconds = float(
            settings.OPENAI_EMBEDDING_TIMEOUT_SECONDS
        )

        if not self.model:
            raise EmbeddingConfigurationError(
                "Embedding model is empty."
            )

        if (
            self.dimensions
            != EMBEDDING_DIMENSIONS
        ):
            raise EmbeddingConfigurationError(
                "Embedding dimension mismatch: "
                f"config={self.dimensions} "
                f"database={EMBEDDING_DIMENSIONS}"
            )

        if self.timeout_seconds <= 0:
            raise EmbeddingConfigurationError(
                "OPENAI_EMBEDDING_TIMEOUT_SECONDS "
                "must be greater than zero."
            )

        if client is not None:
            self.client = client
            return

        if self.provider == "gemini":
            api_key = (
                settings.GEMINI_API_KEY.strip()
            )

            if not api_key:
                raise EmbeddingConfigurationError(
                    "GEMINI_API_KEY is not configured."
                )

            self.client = genai.Client(
                api_key=api_key
            )

            return

        api_key = (
            settings.OPENAI_API_KEY.strip()
        )

        if not api_key:
            raise EmbeddingConfigurationError(
                "OPENAI_API_KEY is not configured."
            )

        base_url = (
            settings.OPENAI_EMBEDDING_BASE_URL
            .strip()
        )

        client_kwargs = {
            "api_key": api_key,
            "timeout": self.timeout_seconds,
        }

        if base_url:
            client_kwargs["base_url"] = (
                base_url
            )

        self.client = OpenAI(
            **client_kwargs
        )

    def generate_embedding(
        self,
        text: str,
    ) -> list[float]:
        """
        Generate one fixed-size embedding.

        Provider-wide failures are converted into
        EmbeddingProviderUnavailableError so batch orchestration
        can stop without consuming the retry budget of every
        queued snapshot.
        """
        input_text = (
            normalize_embedding_text(
                text
            )
        )

        try:
            if self.provider == "gemini":
                response = (
                    self.client.models.embed_content(
                        model=self.model,
                        contents=input_text,
                        config=genai_types.EmbedContentConfig(
                            output_dimensionality=(
                                self.dimensions
                            ),
                        ),
                    )
                )

                embeddings = (
                    response.embeddings
                    or []
                )

                if not embeddings:
                    raise EmbeddingResponseError(
                        "Gemini returned no embeddings."
                    )

                embedding = list(
                    embeddings[0].values
                    or []
                )

            else:
                response = (
                    self.client.embeddings.create(
                        input=input_text,
                        model=self.model,
                        dimensions=self.dimensions,
                        encoding_format="float",
                    )
                )

                embedding = list(
                    response.data[0].embedding
                )
        except GeminiServerError as exc:
            raise EmbeddingProviderUnavailableError(
                "Gemini embedding provider is unavailable: "
                f"status={exc.status} "
                f"code={exc.code}."
            ) from exc

        except GeminiClientError as exc:
            if exc.code in {
                401,
                403,
                429,
            }:
                raise EmbeddingProviderUnavailableError(
                    "Gemini embedding provider is unavailable: "
                    f"status={exc.status} "
                    f"code={exc.code}."
                ) from exc

            raise
        except (
            AuthenticationError,
            PermissionDeniedError,
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
        ) as exc:
            raise EmbeddingProviderUnavailableError(
                "Embedding provider is unavailable."
            ) from exc

        except RateLimitError as exc:
            error_code = (
                _openai_error_code(
                    exc
                )
            )

            if error_code in {
                "insufficient_quota",
                "credit_balance_exhausted",
                "billing_hard_limit_reached",
            }:
                raise EmbeddingProviderUnavailableError(
                    "Embedding provider quota is exhausted "
                    f"(code={error_code})."
                ) from exc

            raise

        if (
            self.provider == "openai"
            and (
                not response.data
                or response.data[0] is None
            )
        ):
            raise EmbeddingResponseError(
                "Embedding provider returned no data."
            )

        if (
            len(embedding)
            != self.dimensions
        ):
            raise EmbeddingResponseError(
                "Embedding provider returned "
                "unexpected dimensions: "
                f"expected={self.dimensions} "
                f"actual={len(embedding)}"
            )

        if not all(
            math.isfinite(float(value))
            for value in embedding
        ):
            raise EmbeddingResponseError(
                "Embedding contains non-finite values."
            )

        return [
            float(value)
            for value in embedding
        ]

    def embed_snapshot(
        self,
        db: Session,
        snapshot: PageSnapshot,
    ) -> PageSnapshot:
        """
        Generate and persist an embedding for one snapshot.

        Lifecycle:
            pending/failed
                -> processing
                -> completed

        Provider/content failures:
            processing
                -> failed

        The processing state is committed before the external API call so
        another worker can observe that the snapshot is being processed.
        """
        snapshot.embedding_status = (
            EMBEDDING_STATUS_PROCESSING
        )

        snapshot.embedding_error = None

        snapshot.embedding_attempts = (
            int(
                snapshot.embedding_attempts
                or 0
            )
            + 1
        )

        db.add(
            snapshot
        )

        db.commit()

        db.refresh(
            snapshot
        )

        try:
            content = (
                load_snapshot_content(
                    snapshot
                )
            )

            embedding = (
                self.generate_embedding(
                    content
                )
            )

            snapshot.embedding = embedding

            snapshot.embedding_model = (
                self.model
            )

            snapshot.embedding_dimensions = (
                self.dimensions
            )

            snapshot.embedded_at = (
                utc_now()
            )

            snapshot.embedding_status = (
                EMBEDDING_STATUS_COMPLETED
            )

            snapshot.embedding_error = None

            db.add(
                snapshot
            )

            db.commit()

            db.refresh(
                snapshot
            )

            logger.info(
                "Snapshot embedding completed: "
                "snapshot=%s model=%s dimensions=%s",
                snapshot.id,
                self.model,
                self.dimensions,
            )

            return snapshot

        except Exception as exc:
            db.rollback()

            try:
                snapshot.embedding = None
                snapshot.embedding_model = None
                snapshot.embedding_dimensions = None
                snapshot.embedded_at = None
                snapshot.embedding_status = (
                    EMBEDDING_STATUS_FAILED
                )
                snapshot.embedding_error = (
                    str(exc)[:2000]
                )

                db.add(
                    snapshot
                )

                db.commit()

                db.refresh(
                    snapshot
                )

            except Exception:
                db.rollback()

                logger.exception(
                    "Unable to persist failed embedding "
                    "state for snapshot %s.",
                    snapshot.id,
                )

            logger.exception(
                "Snapshot embedding failed: snapshot=%s",
                snapshot.id,
            )

            raise


def cosine_similarity(
    left: Iterable[float],
    right: Iterable[float],
) -> float:
    """
    Calculate cosine similarity without numpy.
    """
    left_values = [
        float(value)
        for value in left
    ]

    right_values = [
        float(value)
        for value in right
    ]

    if (
        len(left_values)
        != len(right_values)
    ):
        raise ValueError(
            "Embedding dimensions do not match."
        )

    if not left_values:
        raise ValueError(
            "Embeddings cannot be empty."
        )

    dot_product = sum(
        left_value * right_value
        for left_value, right_value
        in zip(
            left_values,
            right_values,
        )
    )

    left_norm = math.sqrt(
        sum(
            value * value
            for value in left_values
        )
    )

    right_norm = math.sqrt(
        sum(
            value * value
            for value in right_values
        )
    )

    denominator = (
        left_norm
        * right_norm
    )

    if denominator == 0:
        raise ValueError(
            "Cannot calculate cosine similarity "
            "for a zero vector."
        )

    similarity = (
        dot_product
        / denominator
    )

    return max(
        -1.0,
        min(
            1.0,
            similarity,
        ),
    )


def calculate_vector_metrics(
    old_embedding: Iterable[float],
    new_embedding: Iterable[float],
) -> dict[str, float]:
    """
    Calculate deterministic vector drift metrics.

    semantic_distance:
        cosine distance = 1 - cosine similarity

    semantic_magnitude:
        normalized cosine distance in the [0, 1] interval.

    Cosine distance can theoretically range from 0 to 2, so magnitude
    divides by 2 before clamping.
    """
    similarity = cosine_similarity(
        old_embedding,
        new_embedding,
    )

    distance = (
        1.0
        - similarity
    )

    magnitude = max(
        0.0,
        min(
            1.0,
            distance / 2.0,
        ),
    )

    return {
        "semantic_similarity": similarity,
        "semantic_distance": distance,
        "semantic_magnitude": magnitude,
    }