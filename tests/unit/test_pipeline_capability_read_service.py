"""Capability inventory describes deployment intent without probing providers."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.api.pipeline_capability_responses import vector_capabilities_response
from src.services.pipeline_capability_read_service import (
    VECTOR_CAPABILITY_FLAGS,
    get_vector_capability_overview,
)


DEFAULTS = {
    "VECTOR_WORKER_EMBEDDINGS_ENABLED": "false",
    "VECTOR_WORKER_ANALYSIS_ENABLED": "true",
    "VECTOR_WORKER_RISK_SCORING_ENABLED": "true",
    "VECTOR_WORKER_PATTERN_DETECTION_ENABLED": "true",
    "VECTOR_WORKER_ANOMALY_DETECTION_ENABLED": "true",
    "EMBEDDING_PROVIDER": "gemini",
    "GEMINI_API_KEY": "should-not-leak",
}


def _overview(status="healthy"):
    return SimpleNamespace(workers=(SimpleNamespace(
        worker_kind="vector", status=status,
    ),))


def _capability(result, name):
    return next(item for item in result.capabilities if item.name == name)


def test_default_vector_flags_keep_embeddings_disabled():
    result = get_vector_capability_overview(_overview(), environment={})
    assert result.embedding_provider == "openai"
    assert _capability(result, "embedding_generation").status == "disabled"
    assert all(cap.status == "configured" for cap in result.capabilities[1:])
    assert result.basis == "api_configuration_and_worker_heartbeat"


def test_gemini_credential_present_with_healthy_worker():
    result = get_vector_capability_overview(_overview(), environment={
        **DEFAULTS, "VECTOR_WORKER_EMBEDDINGS_ENABLED": "true",
    })
    item = _capability(result, "embedding_generation")
    assert item.status == "configured"
    assert item.requested is True
    assert item.reason is None


@pytest.mark.parametrize("provider,key_name", [
    ("openai", "OPENAI_API_KEY"),
    ("gemini", "GEMINI_API_KEY"),
])
def test_missing_provider_key_is_misconfigured(provider, key_name):
    env = {
        **DEFAULTS,
        "EMBEDDING_PROVIDER": provider,
        "GEMINI_API_KEY": "",
        "VECTOR_WORKER_EMBEDDINGS_ENABLED": "true",
    }
    env.pop("OPENAI_API_KEY", None)
    result = get_vector_capability_overview(_overview(), environment=env)
    assert result.embedding_provider == provider
    item = _capability(result, "embedding_generation")
    assert item.status == "misconfigured"
    assert item.reason == "missing_embedding_credential"
    assert key_name not in str(vector_capabilities_response(result).model_dump())


def test_provider_name_normalization_matches_embedding_service():
    result = get_vector_capability_overview(_overview(), environment={
        **DEFAULTS,
        "EMBEDDING_PROVIDER": "  GEMINI ",
        "VECTOR_WORKER_EMBEDDINGS_ENABLED": "true",
    })
    assert result.embedding_provider == "gemini"
    assert _capability(result, "embedding_generation").status == "configured"


def test_unrecognized_embedding_provider_is_not_echoed():
    result = get_vector_capability_overview(_overview(), environment={
        **DEFAULTS,
        "EMBEDDING_PROVIDER": "malicious-provider-secret-string",
        "VECTOR_WORKER_EMBEDDINGS_ENABLED": "true",
    })
    assert result.embedding_provider is None
    item = _capability(result, "embedding_generation")
    assert item.status == "misconfigured"
    assert item.reason == "unsupported_embedding_provider"
    assert "malicious-provider" not in str(vector_capabilities_response(result).model_dump())


@pytest.mark.parametrize("raw", ["TRUE", "1", "yes", " true ", "", "False"])
def test_invalid_toggle_does_not_claim_capability_active(raw):
    result = get_vector_capability_overview(_overview(), environment={
        **DEFAULTS,
        "VECTOR_WORKER_ANALYSIS_ENABLED": raw,
    })
    item = _capability(result, "vector_analysis")
    assert item.requested is None
    assert item.status == "misconfigured"
    assert item.reason == "invalid_toggle"


@pytest.mark.parametrize("status,expected", [
    ("healthy", "configured"),
    ("degraded", "worker_degraded"),
    ("unavailable", "worker_unavailable"),
    ("disabled", "worker_unavailable"),
])
def test_enabled_capability_reflects_worker_state(status, expected):
    result = get_vector_capability_overview(_overview(status), environment=DEFAULTS)
    assert _capability(result, "vector_analysis").status == expected


def test_disabled_stage_remains_disabled_when_worker_unavailable():
    result = get_vector_capability_overview(_overview("unavailable"), environment=DEFAULTS)
    item = _capability(result, "embedding_generation")
    assert item.status == "disabled"
    assert item.reason == "disabled_by_configuration"


def test_no_vector_worker_record_is_unavailable():
    result = get_vector_capability_overview(
        SimpleNamespace(workers=()), environment=DEFAULTS,
    )
    assert result.worker_status == "unavailable"
    assert _capability(result, "vector_analysis").status == "worker_unavailable"


def test_missing_credential_takes_precedence_over_healthy_heartbeat():
    result = get_vector_capability_overview(_overview(), environment={
        **DEFAULTS,
        "VECTOR_WORKER_EMBEDDINGS_ENABLED": "true",
        "GEMINI_API_KEY": "   ",
    })
    assert _capability(result, "embedding_generation").status == "misconfigured"


def test_reading_capabilities_does_not_expose_credentials():
    env = {**DEFAULTS, "VECTOR_WORKER_EMBEDDINGS_ENABLED": "true",
           "GEMINI_API_KEY": "really-unique-sensitive-token"}
    serialized = vector_capabilities_response(
        get_vector_capability_overview(_overview(), environment=env)
    ).model_dump_json()
    assert "really-unique-sensitive-token" not in serialized
    assert "GEMINI_API_KEY" not in serialized
    assert "EMBEDDING_PROVIDER" not in serialized


def test_inventory_has_exactly_compose_controlled_stages():
    result = get_vector_capability_overview(_overview(), environment=DEFAULTS)
    assert [c.name for c in result.capabilities] == [flag[0] for flag in VECTOR_CAPABILITY_FLAGS]
    assert len(result.capabilities) == 5


def test_compose_mirrors_every_vector_toggle_to_api():
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()
    api_section = compose.split("\n  api:\n", 1)[1].split("\n  vector_worker:\n", 1)[0]
    vector_section = compose.split("\n  vector_worker:\n", 1)[1].split("\n  alert_delivery_worker:\n", 1)[0]
    for _name, key, default in VECTOR_CAPABILITY_FLAGS:
        expected = f"{key}: ${{{key}:-{'true' if default else 'false'}}}"
        assert expected in api_section
        assert expected in vector_section
