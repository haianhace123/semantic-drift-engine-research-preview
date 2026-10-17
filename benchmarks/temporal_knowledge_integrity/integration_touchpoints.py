from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TouchpointBurden(str, Enum):
    CUSTOMER_CODE_CHANGE = "customer_code_change"
    ADAPTER_HOOK = "adapter_hook"
    NO_TOUCH = "no_touch"


@dataclass(frozen=True)
class IntegrationTouchpoint:
    name: str
    burden: TouchpointBurden
    captures: tuple[str, ...]


@dataclass(frozen=True)
class IntegrationTouchpointReport:
    touchpoints: tuple[IntegrationTouchpoint, ...]

    @property
    def customer_code_changes(self) -> int:
        return sum(
            touchpoint.burden
            is TouchpointBurden.CUSTOMER_CODE_CHANGE
            for touchpoint in self.touchpoints
        )

    @property
    def adapter_hooks(self) -> int:
        return sum(
            touchpoint.burden
            is TouchpointBurden.ADAPTER_HOOK
            for touchpoint in self.touchpoints
        )

    @property
    def untouched_boundaries(self) -> int:
        return sum(
            touchpoint.burden
            is TouchpointBurden.NO_TOUCH
            for touchpoint in self.touchpoints
        )


def build_rag_touchpoint_report() -> IntegrationTouchpointReport:
    return IntegrationTouchpointReport(
        touchpoints=(
            IntegrationTouchpoint(
                name="source_snapshot",
                burden=TouchpointBurden.NO_TOUCH,
                captures=(
                    "source_external_id",
                    "snapshot_external_id",
                ),
            ),
            IntegrationTouchpoint(
                name="chunk_identity",
                burden=TouchpointBurden.CUSTOMER_CODE_CHANGE,
                captures=(
                    "chunk_asset_external_id",
                ),
            ),
            IntegrationTouchpoint(
                name="pre_lossy_materialization",
                burden=TouchpointBurden.ADAPTER_HOOK,
                captures=(
                    "index_asset_external_id",
                    "consumed_chunk_revisions",
                ),
            ),
            IntegrationTouchpoint(
                name="consumer_revision_binding",
                burden=TouchpointBurden.ADAPTER_HOOK,
                captures=(
                    "agent_asset_external_id",
                    "consumed_index_revision",
                    "critical",
                ),
            ),
        )
    )
