from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.domain.lineage_revision import (
    LINEAGE_EXTERNAL_ID_MAX_LENGTH,
    LineageRevisionIdentity,
)

LINEAGE_INVALIDATION_REASON_CODE_MAX_LENGTH = 128

LineageInvalidationDisposition = Literal[
    "no_action",
    "invalidate",
    "rematerialize",
]

@dataclass(frozen=True)
class LineageInvalidationPolicyIdentity:
    policy_external_id: str
    revision: int

    def __post_init__(self) -> None:
        normalized_policy_external_id = (
            self.policy_external_id.strip()
        )

        if not normalized_policy_external_id:
            raise ValueError(
                "policy_external_id must not be empty."
            )

        if (
            len(normalized_policy_external_id)
            > LINEAGE_EXTERNAL_ID_MAX_LENGTH
        ):
            raise ValueError(
                "policy_external_id must not exceed "
                f"{LINEAGE_EXTERNAL_ID_MAX_LENGTH} characters."
            )

        if self.revision < 0:
            raise ValueError(
                "policy revision must not be negative."
            )

        object.__setattr__(
            self,
            "policy_external_id",
            normalized_policy_external_id,
        )

@dataclass(frozen=True)
class LineageInvalidationDecision:
    root: LineageRevisionIdentity
    target: LineageRevisionIdentity
    policy: LineageInvalidationPolicyIdentity
    disposition: LineageInvalidationDisposition
    reason_code: str
    causal_path: tuple[LineageRevisionIdentity, ...]

    def __post_init__(self) -> None:
        if self.disposition not in (
            "no_action",
            "invalidate",
            "rematerialize",
        ):
            raise ValueError(
                "Unsupported invalidation disposition."
            )

        normalized_reason_code = self.reason_code.strip()

        if not normalized_reason_code:
            raise ValueError(
                "reason_code must not be empty."
            )

        if (
            len(normalized_reason_code)
            > LINEAGE_INVALIDATION_REASON_CODE_MAX_LENGTH
        ):
            raise ValueError(
                "reason_code must not exceed "
                f"{LINEAGE_INVALIDATION_REASON_CODE_MAX_LENGTH} "
                "characters."
            )

        object.__setattr__(
            self,
            "reason_code",
            normalized_reason_code,
        )

        if not self.causal_path:
            raise ValueError(
                "causal_path must not be empty."
            )

        if self.causal_path[0] != self.root:
            raise ValueError(
                "causal_path must start with root."
            )

        if self.causal_path[-1] != self.target:
            raise ValueError(
                "causal_path must end with target."
            )

        if self.root == self.target:
            raise ValueError(
                "Invalidation decision target must differ from root."
            )

        if len(set(self.causal_path)) != len(
            self.causal_path
        ):
            raise ValueError(
                "causal_path must not contain repeated revisions."
            )