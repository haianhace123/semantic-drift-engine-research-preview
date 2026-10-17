from __future__ import annotations

from dataclasses import dataclass


LINEAGE_EXTERNAL_ID_MAX_LENGTH = 512


@dataclass(frozen=True)
class LineageRevisionIdentity:
    source_external_id: str
    asset_external_id: str
    revision_external_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "source_external_id",
            "asset_external_id",
            "revision_external_id",
        ):
            value = getattr(self, field_name)
            normalized = value.strip()

            if not normalized:
                raise ValueError(
                    f"{field_name} must not be empty."
                )

            if len(normalized) > LINEAGE_EXTERNAL_ID_MAX_LENGTH:
                raise ValueError(
                    f"{field_name} must not exceed "
                    f"{LINEAGE_EXTERNAL_ID_MAX_LENGTH} characters."
                )

            object.__setattr__(
                self,
                field_name,
                normalized,
            )