from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


_SECRET_CONFIGURATION_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "password",
        "secret",
        "secret_key",
        "token",
        "access_token",
    }
)


@dataclass(frozen=True)
class LineageExecutionTargetIdentity:
    source_external_id: str
    asset_external_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_external_id",
            _required_string(
                self.source_external_id,
                field_name="source_external_id",
            ),
        )
        object.__setattr__(
            self,
            "asset_external_id",
            _required_string(
                self.asset_external_id,
                field_name="asset_external_id",
            ),
        )


@dataclass(frozen=True)
class LineageExecutionTargetBinding:
    identity: LineageExecutionTargetIdentity
    binding_revision: int
    provider: str
    target_kind: str
    configuration: Mapping[str, str]

    def __post_init__(self) -> None:
        if self.binding_revision <= 0:
            raise ValueError(
                "binding_revision must be greater than zero."
            )

        object.__setattr__(
            self,
            "provider",
            _required_string(
                self.provider,
                field_name="provider",
            ),
        )
        object.__setattr__(
            self,
            "target_kind",
            _required_string(
                self.target_kind,
                field_name="target_kind",
            ),
        )

        normalized_configuration = {
            _required_string(
                key,
                field_name="configuration key",
            ): _required_string(
                value,
                field_name=f"configuration[{key!r}]",
            )
            for key, value in self.configuration.items()
        }

        forbidden_keys = {
            key
            for key in normalized_configuration
            if key.lower() in _SECRET_CONFIGURATION_KEYS
        }

        if forbidden_keys:
            raise ValueError(
                "Execution target configuration must not contain "
                "secret credentials."
            )

        object.__setattr__(
            self,
            "configuration",
            normalized_configuration,
        )


def _required_string(
    value: str,
    *,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        raise TypeError(
            f"{field_name} must be a string."
        )

    normalized = value.strip()

    if not normalized:
        raise ValueError(
            f"{field_name} must not be empty."
        )

    return normalized