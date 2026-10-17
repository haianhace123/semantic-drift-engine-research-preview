from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MetadataOwnership(str, Enum):
    CUSTOMER_REQUIRED = "customer_required"
    CUSTOMER_OR_ADAPTER = "customer_or_adapter"
    SDE_DERIVED = "sde_derived"


@dataclass(frozen=True)
class InstrumentationField:
    name: str
    ownership: MetadataOwnership
    purpose: str


@dataclass(frozen=True)
class MaterializationBoundary:
    name: str
    fields: tuple[InstrumentationField, ...]

    @property
    def customer_required_count(self) -> int:
        return sum(
            field.ownership
            is MetadataOwnership.CUSTOMER_REQUIRED
            for field in self.fields
        )

    @property
    def customer_or_adapter_count(self) -> int:
        return sum(
            field.ownership
            is MetadataOwnership.CUSTOMER_OR_ADAPTER
            for field in self.fields
        )

    @property
    def sde_derived_count(self) -> int:
        return sum(
            field.ownership
            is MetadataOwnership.SDE_DERIVED
            for field in self.fields
        )


@dataclass(frozen=True)
class InstrumentationBurden:
    boundaries: tuple[MaterializationBoundary, ...]

    @property
    def customer_required_fields(self) -> int:
        return sum(
            boundary.customer_required_count
            for boundary in self.boundaries
        )

    @property
    def customer_or_adapter_fields(self) -> int:
        return sum(
            boundary.customer_or_adapter_count
            for boundary in self.boundaries
        )

    @property
    def sde_derived_fields(self) -> int:
        return sum(
            boundary.sde_derived_count
            for boundary in self.boundaries
        )

    @property
    def total_fields(self) -> int:
        return sum(
            len(boundary.fields)
            for boundary in self.boundaries
        )


def build_rag_instrumentation_burden() -> InstrumentationBurden:
    return InstrumentationBurden(
        boundaries=(
            MaterializationBoundary(
                name="chunk",
                fields=(
                    InstrumentationField(
                        name="source_external_id",
                        ownership=MetadataOwnership.CUSTOMER_OR_ADAPTER,
                        purpose="Identify the upstream knowledge source.",
                    ),
                    InstrumentationField(
                        name="snapshot_external_id",
                        ownership=MetadataOwnership.SDE_DERIVED,
                        purpose="Bind materialization to SDE source truth.",
                    ),
                    InstrumentationField(
                        name="asset_external_id",
                        ownership=MetadataOwnership.CUSTOMER_REQUIRED,
                        purpose="Preserve stable logical chunk identity.",
                    ),
                    InstrumentationField(
                        name="revision_external_id",
                        ownership=MetadataOwnership.SDE_DERIVED,
                        purpose="Identify the exact chunk revision.",
                    ),
                ),
            ),
            MaterializationBoundary(
                name="index",
                fields=(
                    InstrumentationField(
                        name="asset_external_id",
                        ownership=MetadataOwnership.CUSTOMER_OR_ADAPTER,
                        purpose="Identify the logical index.",
                    ),
                    InstrumentationField(
                        name="revision_external_id",
                        ownership=MetadataOwnership.SDE_DERIVED,
                        purpose="Identify the materialized index revision.",
                    ),
                    InstrumentationField(
                        name="consumed_chunk_revisions",
                        ownership=MetadataOwnership.CUSTOMER_REQUIRED,
                        purpose="Record exact chunk revisions indexed.",
                    ),
                ),
            ),
            MaterializationBoundary(
                name="agent",
                fields=(
                    InstrumentationField(
                        name="asset_external_id",
                        ownership=MetadataOwnership.CUSTOMER_OR_ADAPTER,
                        purpose="Identify the consuming AI surface.",
                    ),
                    InstrumentationField(
                        name="revision_external_id",
                        ownership=MetadataOwnership.SDE_DERIVED,
                        purpose="Identify the consuming surface revision.",
                    ),
                    InstrumentationField(
                        name="consumed_index_revision",
                        ownership=MetadataOwnership.CUSTOMER_REQUIRED,
                        purpose="Record the exact index revision consumed.",
                    ),
                    InstrumentationField(
                        name="critical",
                        ownership=MetadataOwnership.CUSTOMER_OR_ADAPTER,
                        purpose="Mark business-critical AI surfaces.",
                    ),
                ),
            ),
        )
    )
