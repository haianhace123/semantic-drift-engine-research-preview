"""Response schema for the admin-only vector capability inventory."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.services.pipeline_capability_read_service import VectorCapabilityOverview


class VectorCapabilityResponse(BaseModel):
    name: str
    requested: bool | None
    status: Literal[
        "disabled",
        "misconfigured",
        "worker_unavailable",
        "worker_degraded",
        "configured",
    ]
    reason: str | None


class VectorCapabilitiesResponse(BaseModel):
    basis: Literal["api_configuration_and_worker_heartbeat"]
    worker_status: Literal["healthy", "degraded", "unavailable", "disabled"]
    embedding_provider: Literal["openai", "gemini"] | None
    capabilities: list[VectorCapabilityResponse]


def vector_capabilities_response(
    overview: VectorCapabilityOverview,
) -> VectorCapabilitiesResponse:
    return VectorCapabilitiesResponse(
        basis=overview.basis,
        worker_status=overview.worker_status,
        embedding_provider=overview.embedding_provider,
        capabilities=[
            VectorCapabilityResponse(
                name=item.name,
                requested=item.requested,
                status=item.status,
                reason=item.reason,
            )
            for item in overview.capabilities
        ],
    )
