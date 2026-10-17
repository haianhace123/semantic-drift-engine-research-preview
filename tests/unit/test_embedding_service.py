from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import json

import requests

from src.models.page_snapshot import EMBEDDING_DIMENSIONS
from src.services.embedding_service import (
    EmbeddingProviderUnavailableError,
    EmbeddingResponseError,
    EmbeddingService,
    SnapshotContentError,
    calculate_vector_metrics,
    cosine_similarity,
    normalize_embedding_text,
    EmbeddingConfigurationError,
)

from google.genai.errors import (
    ClientError as GeminiClientError,
    ServerError as GeminiServerError,
)

from unittest.mock import MagicMock, patch

@pytest.fixture(autouse=True)
def use_openai_embedding_provider(
    monkeypatch,
):
    """
    Preserve the historical OpenAI unit-test baseline regardless of
    the embedding provider selected in the developer's local .env.

    Gemini-specific tests override this setting explicitly.
    """
    monkeypatch.setattr(
        "src.services.embedding_service.settings.EMBEDDING_PROVIDER",
        "openai",
    )

class FakeEmbeddingsAPI:
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
        self.calls.append(kwargs)

        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    embedding=self.embedding
                )
            ]
        )


class FakeOpenAIClient:
    def __init__(
        self,
        embedding: list[float],
    ) -> None:
        self.embeddings = FakeEmbeddingsAPI(
            embedding
        )

class RaisingEmbeddingsAPI:
    def __init__(
        self,
        error: Exception,
    ) -> None:
        self.error = error

    def create(
        self,
        **kwargs,
    ):
        raise self.error


class RaisingOpenAIClient:
    def __init__(
        self,
        error: Exception,
    ) -> None:
        self.embeddings = (
            RaisingEmbeddingsAPI(
                error
            )
        )

def _vector(
    index: int = 0,
) -> list[float]:
    values = [
        0.0
        for _ in range(
            EMBEDDING_DIMENSIONS
        )
    ]

    values[index] = 1.0

    return values


def test_generate_embedding_uses_injected_client():
    expected = _vector(0)

    client = FakeOpenAIClient(
        expected
    )

    service = EmbeddingService(
        client=client
    )

    result = service.generate_embedding(
        "Semantic drift test content."
    )

    assert result == expected

    assert len(
        client.embeddings.calls
    ) == 1

    call = (
        client.embeddings.calls[0]
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
        call["input"]
        == "Semantic drift test content."
    )


def test_generate_embedding_rejects_wrong_dimensions():
    client = FakeOpenAIClient(
        [1.0, 0.0]
    )

    service = EmbeddingService(
        client=client
    )

    with pytest.raises(
        EmbeddingResponseError,
        match="unexpected dimensions",
    ):
        service.generate_embedding(
            "Dimension validation."
        )


def test_generate_embedding_rejects_non_finite_values():
    embedding = _vector(0)

    embedding[10] = float(
        "nan"
    )

    client = FakeOpenAIClient(
        embedding
    )

    service = EmbeddingService(
        client=client
    )

    with pytest.raises(
        EmbeddingResponseError,
        match="non-finite",
    ):
        service.generate_embedding(
            "Finite validation."
        )


def test_normalize_embedding_text_rejects_empty_input():
    with pytest.raises(
        SnapshotContentError,
        match="empty",
    ):
        normalize_embedding_text(
            "   "
        )


def test_cosine_similarity_identical_vectors():
    left = _vector(0)
    right = _vector(0)

    result = cosine_similarity(
        left,
        right,
    )

    assert result == pytest.approx(
        1.0
    )


def test_cosine_similarity_orthogonal_vectors():
    left = _vector(0)
    right = _vector(1)

    result = cosine_similarity(
        left,
        right,
    )

    assert result == pytest.approx(
        0.0
    )


def test_calculate_vector_metrics_identical_vectors():
    metrics = calculate_vector_metrics(
        _vector(0),
        _vector(0),
    )

    assert (
        metrics["semantic_similarity"]
        == pytest.approx(1.0)
    )

    assert (
        metrics["semantic_distance"]
        == pytest.approx(0.0)
    )

    assert (
        metrics["semantic_magnitude"]
        == pytest.approx(0.0)
    )


def test_calculate_vector_metrics_orthogonal_vectors():
    metrics = calculate_vector_metrics(
        _vector(0),
        _vector(1),
    )

    assert (
        metrics["semantic_similarity"]
        == pytest.approx(0.0)
    )

    assert (
        metrics["semantic_distance"]
        == pytest.approx(1.0)
    )

    assert (
        metrics["semantic_magnitude"]
        == pytest.approx(0.5)
    )


def test_calculate_vector_metrics_opposite_vectors():
    left = _vector(0)

    right = [
        -value
        for value in left
    ]

    metrics = calculate_vector_metrics(
        left,
        right,
    )

    assert (
        metrics["semantic_similarity"]
        == pytest.approx(-1.0)
    )

    assert (
        metrics["semantic_distance"]
        == pytest.approx(2.0)
    )

    assert (
        metrics["semantic_magnitude"]
        == pytest.approx(1.0)
    )


def test_cosine_similarity_rejects_dimension_mismatch():
    with pytest.raises(
        ValueError,
        match="dimensions do not match",
    ):
        cosine_similarity(
            [1.0, 0.0],
            [1.0],
        )


def test_cosine_similarity_rejects_zero_vector():
    with pytest.raises(
        ValueError,
        match="zero vector",
    ):
        cosine_similarity(
            [0.0, 0.0],
            [1.0, 0.0],
        )

def test_generate_embedding_wraps_authentication_error():
    from openai import AuthenticationError

    error = AuthenticationError(
        "invalid api key",
        response=SimpleNamespace(
            status_code=401,
            headers={},
            request=SimpleNamespace(),
        ),
        body={
            "error": {
                "message": "invalid api key"
            }
        },
    )

    service = EmbeddingService(
        client=RaisingOpenAIClient(
            error
        )
    )

    with pytest.raises(
        EmbeddingProviderUnavailableError,
        match="unavailable",
    ):
        service.generate_embedding(
            "Authentication failure."
        )


def test_generate_embedding_wraps_connection_error():
    from openai import APIConnectionError

    error = APIConnectionError(
        request=SimpleNamespace()
    )

    service = EmbeddingService(
        client=RaisingOpenAIClient(
            error
        )
    )

    with pytest.raises(
        EmbeddingProviderUnavailableError,
        match="unavailable",
    ):
        service.generate_embedding(
            "Connection failure."
        )


def test_generate_embedding_wraps_insufficient_quota():
    from openai import RateLimitError

    error = RateLimitError(
        "quota exhausted",
        response=SimpleNamespace(
            status_code=429,
            headers={},
            request=SimpleNamespace(),
        ),
        body={
            "error": {
                "message": "quota exhausted",
                "code": "insufficient_quota",
            }
        },
    )

    service = EmbeddingService(
        client=RaisingOpenAIClient(
            error
        )
    )

    with pytest.raises(
        EmbeddingProviderUnavailableError,
        match=r"code=insufficient_quota",
    ):
        service.generate_embedding(
            "Quota failure."
        )

def test_generate_embedding_wraps_credit_balance_exhausted():
    from openai import RateLimitError

    error = RateLimitError(
        "credit exhausted",
        response=SimpleNamespace(
            status_code=429,
            headers={},
            request=SimpleNamespace(),
        ),
        body={
            "error": {
                "message": "credit exhausted",
                "code": "credit_balance_exhausted",
            }
        },
    )

    service = EmbeddingService(
        client=RaisingOpenAIClient(
            error
        )
    )

    with pytest.raises(
        EmbeddingProviderUnavailableError,
        match=r"code=credit_balance_exhausted",
    ):
        service.generate_embedding(
            "Credit failure."
        )

def test_generate_embedding_preserves_temporary_rate_limit():
    from openai import RateLimitError

    error = RateLimitError(
        "rate limited",
        response=SimpleNamespace(
            status_code=429,
            headers={},
            request=SimpleNamespace(),
        ),
        body={
            "error": {
                "message": "rate limited",
                "code": "rate_limit_exceeded",
            }
        },
    )

    service = EmbeddingService(
        client=RaisingOpenAIClient(
            error
        )
    )

    with pytest.raises(
        RateLimitError
    ):
        service.generate_embedding(
            "Temporary rate limit."
        )

def test_embedding_service_uses_default_provider_url_when_base_url_empty(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.services.embedding_service.settings.OPENAI_API_KEY",
        "test-api-key",
    )

    monkeypatch.setattr(
        "src.services.embedding_service.settings.OPENAI_EMBEDDING_BASE_URL",
        "",
    )

    monkeypatch.setattr(
        "src.services.embedding_service.settings.OPENAI_EMBEDDING_TIMEOUT_SECONDS",
        12.5,
    )

    with patch(
        "src.services.embedding_service.OpenAI"
    ) as openai_cls:
        EmbeddingService()

    openai_cls.assert_called_once_with(
        api_key="test-api-key",
        timeout=12.5,
    )


def test_embedding_service_uses_configured_base_url(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.services.embedding_service.settings.OPENAI_API_KEY",
        "test-api-key",
    )

    monkeypatch.setattr(
        "src.services.embedding_service.settings.OPENAI_EMBEDDING_BASE_URL",
        "https://embedding.example.test/v1",
    )

    monkeypatch.setattr(
        "src.services.embedding_service.settings.OPENAI_EMBEDDING_TIMEOUT_SECONDS",
        12.5,
    )

    with patch(
        "src.services.embedding_service.OpenAI"
    ) as openai_cls:
        EmbeddingService()

    openai_cls.assert_called_once_with(
        api_key="test-api-key",
        timeout=12.5,
        base_url="https://embedding.example.test/v1",
    )

def _gemini_error_response(
    *,
    code: int,
    status: str,
    message: str,
) -> requests.Response:
    response = requests.Response()

    response.status_code = code
    response.reason = status

    response._content = json.dumps(
        {
            "error": {
                "code": code,
                "status": status,
                "message": message,
            }
        }
    ).encode("utf-8")

    response.headers["Content-Type"] = (
        "application/json"
    )

    return response

def test_embedding_service_uses_gemini_provider(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.services.embedding_service.settings.EMBEDDING_PROVIDER",
        "gemini",
    )
    monkeypatch.setattr(
        "src.services.embedding_service.settings.GEMINI_API_KEY",
        "test-gemini-key",
    )
    monkeypatch.setattr(
        "src.services.embedding_service.settings.GEMINI_EMBEDDING_MODEL",
        "gemini-embedding-2",
    )

    with patch(
        "src.services.embedding_service.genai.Client"
    ) as gemini_client_cls:
        service = EmbeddingService()

    gemini_client_cls.assert_called_once_with(
        api_key="test-gemini-key"
    )

    assert service.provider == "gemini"
    assert (
        service.model
        == "gemini-embedding-2"
    )


def test_generate_embedding_uses_gemini_dimensions(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.services.embedding_service.settings.EMBEDDING_PROVIDER",
        "gemini",
    )
    monkeypatch.setattr(
        "src.services.embedding_service.settings.GEMINI_EMBEDDING_MODEL",
        "gemini-embedding-2",
    )

    expected = _vector(0)

    embedding_result = SimpleNamespace(
        values=expected
    )

    response = SimpleNamespace(
        embeddings=[
            embedding_result
        ]
    )

    client = MagicMock()

    client.models.embed_content.return_value = (
        response
    )

    service = EmbeddingService(
        client=client
    )

    result = service.generate_embedding(
        "Gemini embedding test."
    )

    assert result == expected

    client.models.embed_content.assert_called_once()

    call = (
        client.models.embed_content
        .call_args
    )

    assert (
        call.kwargs["model"]
        == "gemini-embedding-2"
    )

    assert (
        call.kwargs["contents"]
        == "Gemini embedding test."
    )

    config = call.kwargs["config"]

    assert (
        config.output_dimensionality
        == EMBEDDING_DIMENSIONS
    )


def test_generate_embedding_rejects_wrong_gemini_dimensions(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.services.embedding_service.settings.EMBEDDING_PROVIDER",
        "gemini",
    )

    response = SimpleNamespace(
        embeddings=[
            SimpleNamespace(
                values=[
                    1.0,
                    0.0,
                ]
            )
        ]
    )

    client = MagicMock()

    client.models.embed_content.return_value = (
        response
    )

    service = EmbeddingService(
        client=client
    )

    with pytest.raises(
        EmbeddingResponseError,
        match="unexpected dimensions",
    ):
        service.generate_embedding(
            "Wrong Gemini dimensions."
        )


def test_embedding_service_rejects_unknown_provider(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.services.embedding_service.settings.EMBEDDING_PROVIDER",
        "unsupported-provider",
    )

    with pytest.raises(
        EmbeddingConfigurationError,
        match="Unsupported embedding provider",
    ):
        EmbeddingService(
            client=MagicMock()
        )

def test_gemini_server_error_is_provider_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.services.embedding_service.settings.EMBEDDING_PROVIDER",
        "gemini",
    )

    client = MagicMock()

    client.models.embed_content.side_effect = (
        GeminiServerError(
            503,
            _gemini_error_response(
                code=503,
                status="UNAVAILABLE",
                message="Service unavailable.",
            ),
        )
    )

    service = EmbeddingService(
        client=client
    )

    with pytest.raises(
        EmbeddingProviderUnavailableError,
        match="Gemini embedding provider is unavailable",
    ):
        service.generate_embedding(
            "Gemini outage test."
        )


def test_gemini_rate_limit_is_provider_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.services.embedding_service.settings.EMBEDDING_PROVIDER",
        "gemini",
    )

    client = MagicMock()

    client.models.embed_content.side_effect = (
        GeminiClientError(
            429,
            _gemini_error_response(
                code=429,
                status="RESOURCE_EXHAUSTED",
                message="Quota exhausted.",
            ),
        )
    )

    service = EmbeddingService(
        client=client
    )

    with pytest.raises(
        EmbeddingProviderUnavailableError,
        match="Gemini embedding provider is unavailable",
    ):
        service.generate_embedding(
            "Gemini quota test."
        )


def test_gemini_bad_request_is_not_provider_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.services.embedding_service.settings.EMBEDDING_PROVIDER",
        "gemini",
    )

    client = MagicMock()

    client.models.embed_content.side_effect = (
        GeminiClientError(
            400,
            _gemini_error_response(
                code=400,
                status="INVALID_ARGUMENT",
                message="Invalid request.",
            ),
        )
    )

    service = EmbeddingService(
        client=client
    )

    with pytest.raises(
        GeminiClientError
    ):
        service.generate_embedding(
            "Gemini invalid request test."
        )