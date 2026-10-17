"""Read-only capability inventory for the Compose vector worker.

This reports deployment intent and recent worker heartbeat, not proof that an
external embedding provider can successfully fulfill requests. It never calls
an embedding provider or returns credential values.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.services.worker_operational_read_service import WorkerOperationalOverview


CapabilityStatus = Literal[
    "disabled",
    "misconfigured",
    "worker_unavailable",
    "worker_degraded",
    "configured",
]


@dataclass(frozen=True)
class VectorCapabilityView:
    name: str
    requested: bool | None
    status: CapabilityStatus
    reason: str | None


@dataclass(frozen=True)
class VectorCapabilityOverview:
    basis: str
    worker_status: str
    embedding_provider: str | None
    capabilities: tuple[VectorCapabilityView, ...]


# These are exactly the five feature flags interpreted by the Compose
# vector_worker command. Internal pipeline stages without a Compose toggle
# are intentionally not included in this inventory.
VECTOR_CAPABILITY_FLAGS = (
    ("embedding_generation", "VECTOR_WORKER_EMBEDDINGS_ENABLED", False),
    ("vector_analysis", "VECTOR_WORKER_ANALYSIS_ENABLED", True),
    ("risk_scoring", "VECTOR_WORKER_RISK_SCORING_ENABLED", True),
    ("pattern_detection", "VECTOR_WORKER_PATTERN_DETECTION_ENABLED", True),
    ("anomaly_detection", "VECTOR_WORKER_ANOMALY_DETECTION_ENABLED", True),
)


def _flag_value(value: str) -> bool | None:
    # Match the Compose shell command's exact comparison to "true".
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def get_vector_capability_overview(
    worker_overview: WorkerOperationalOverview,
    *,
    environment: Mapping[str, str] | None = None,
) -> VectorCapabilityOverview:
    """Report configured capability intent and current vector worker state.

    For non-Compose deployments, the API environment must explicitly mirror
    the vector worker's feature flags. The result is not a runtime feature
    negotiation protocol and cannot confirm that provider quota is available.
    """
    env = os.environ if environment is None else environment
    vector = next(
        (worker for worker in worker_overview.workers
         if worker.worker_kind == "vector"),
        None,
    )
    worker_status = "unavailable" if vector is None else vector.status

    provider_value = env.get("EMBEDDING_PROVIDER", "openai").strip().lower()
    provider = provider_value if provider_value in {"openai", "gemini"} else None
    provider_key_name = (
        "GEMINI_API_KEY" if provider == "gemini" else "OPENAI_API_KEY"
    )

    capabilities: list[VectorCapabilityView] = []
    for name, env_name, default in VECTOR_CAPABILITY_FLAGS:
        raw = env.get(env_name, "true" if default else "false")
        requested = _flag_value(raw)
        if requested is None:
            status: CapabilityStatus = "misconfigured"
            reason = "invalid_toggle"
        elif not requested:
            status = "disabled"
            reason = "disabled_by_configuration"
        elif name == "embedding_generation" and provider is None:
            status = "misconfigured"
            reason = "unsupported_embedding_provider"
        elif (name == "embedding_generation" and
              not env.get(provider_key_name, "").strip()):
            status = "misconfigured"
            reason = "missing_embedding_credential"
        elif worker_status in {"unavailable", "disabled"}:
            status = "worker_unavailable"
            reason = "vector_worker_unavailable"
        elif worker_status == "degraded":
            status = "worker_degraded"
            reason = "vector_worker_degraded"
        else:
            status = "configured"
            reason = None
        capabilities.append(VectorCapabilityView(
            name=name,
            requested=requested,
            status=status,
            reason=reason,
        ))

    return VectorCapabilityOverview(
        basis="api_configuration_and_worker_heartbeat",
        worker_status=worker_status,
        embedding_provider=provider,
        capabilities=tuple(capabilities),
    )
