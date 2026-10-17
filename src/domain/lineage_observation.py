from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


LineageObservationKind = Literal[
    "materialization",
    "consumption",
]

LineageAssetKind = Literal[
    "chunk",
    "index",
    "agent",
    "workflow",
]


@dataclass(frozen=True)
class LineageRevisionReference:
    asset_external_id: str
    revision_external_id: str
    dependency_type: str = "derived_from"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "asset_external_id",
            _required_identity(
                self.asset_external_id,
                field_name="dependency asset_external_id",
            ),
        )
        object.__setattr__(
            self,
            "revision_external_id",
            _required_identity(
                self.revision_external_id,
                field_name="dependency revision_external_id",
            ),
        )
        object.__setattr__(
            self,
            "dependency_type",
            _required_identity(
                self.dependency_type,
                field_name="dependency_type",
            ),
        )

@dataclass(frozen=True)
class LineageObservation:
    observation_kind: LineageObservationKind
    source_external_id: str
    snapshot_external_id: str
    asset_external_id: str
    asset_kind: LineageAssetKind
    revision_external_id: str
    sequence: int
    dependencies: tuple[LineageRevisionReference, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_external_id",
            _required_identity(
                self.source_external_id,
                field_name="source_external_id",
            ),
        )
        object.__setattr__(
            self,
            "snapshot_external_id",
            _required_identity(
                self.snapshot_external_id,
                field_name="snapshot_external_id",
            ),
        )
        object.__setattr__(
            self,
            "asset_external_id",
            _required_identity(
                self.asset_external_id,
                field_name="asset_external_id",
            ),
        )
        object.__setattr__(
            self,
            "revision_external_id",
            _required_identity(
                self.revision_external_id,
                field_name="revision_external_id",
            ),
        )

        if self.sequence < 0:
            raise ValueError("sequence must not be negative.")
        dependency_identities = {
            (
                dependency.asset_external_id,
                dependency.revision_external_id,
                dependency.dependency_type,
            )
            for dependency in self.dependencies
        }

        if len(dependency_identities) != len(self.dependencies):
            raise ValueError(
                "Lineage observation dependencies must be unique."
            )
        if (
            self.observation_kind == "consumption"
            and not self.dependencies
        ):
            raise ValueError(
                "Consumption observations require at least one "
                "exact revision dependency."
            )


@dataclass(frozen=True)
class LineageObservationProjection:
    current_observations: tuple[LineageObservation, ...]
    historical_observations: tuple[LineageObservation, ...]


@dataclass(frozen=True)
class LineageDependencyTransition:
    """Exact dependency changes between two recorded materializations.

    This is a comparison of source-reported revision evidence, not a
    claim that an unobserved upstream relationship ceased to exist.
    """

    before: LineageObservation
    after: LineageObservation
    added: tuple[LineageRevisionReference, ...]
    removed: tuple[LineageRevisionReference, ...]
    unchanged: tuple[LineageRevisionReference, ...]


def compare_materialization_dependencies(
    before: LineageObservation,
    after: LineageObservation,
) -> LineageDependencyTransition:
    """Diff immutable observations for the same materialized subject.

    Full revision identity and dependency type are significant: changing
    a dependency from revision A to B produces one removal and one addition.
    """
    if (
        before.observation_kind != "materialization"
        or after.observation_kind != "materialization"
        or before.source_external_id != after.source_external_id
        or before.asset_external_id != after.asset_external_id
        or before.asset_kind != after.asset_kind
        or before.sequence >= after.sequence
    ):
        raise ValueError(
            "Dependency transition requires ordered materializations "
            "of the same source, asset, and asset kind."
        )

    old = set(before.dependencies)
    new = set(after.dependencies)

    def ordered(values: set[LineageRevisionReference]):
        return tuple(sorted(
            values,
            key=lambda dep: (
                dep.asset_external_id,
                dep.revision_external_id,
                dep.dependency_type,
            ),
        ))

    return LineageDependencyTransition(
        before=before,
        after=after,
        added=ordered(new - old),
        removed=ordered(old - new),
        unchanged=ordered(old & new),
    )


def project_lineage_observations(
    observations: tuple[LineageObservation, ...],
) -> LineageObservationProjection:
    if not observations:
        raise ValueError(
            "At least one lineage observation is required."
        )

    current_by_subject: dict[
        tuple[str, str, LineageObservationKind],
        LineageObservation,
    ] = {}
    historical_by_subject_sequence: dict[
        tuple[str, str, LineageObservationKind, int],
        LineageObservation,
    ] = {}

    for observation in observations:
        subject_key = (
            observation.source_external_id,
            observation.asset_external_id,
            observation.observation_kind,
        )
        current = current_by_subject.get(subject_key)

        if current is None:
            current_by_subject[subject_key] = observation
            continue

        if observation.sequence > current.sequence:
            _record_historical_observation(
                historical_by_subject_sequence,
                current,
            )
            current_by_subject[subject_key] = observation
            continue

        if observation.sequence < current.sequence:
            _record_historical_observation(
                historical_by_subject_sequence,
                observation,
            )
            continue

        if observation != current:
            raise ValueError(
                "Conflicting lineage observations share the same "
                "subject sequence."
            )

    return LineageObservationProjection(
        current_observations=tuple(
            sorted(
                current_by_subject.values(),
                key=_observation_sort_key,
            )
        ),
        historical_observations=tuple(
            sorted(
                historical_by_subject_sequence.values(),
                key=_observation_sort_key,
            )
        ),
    )


def _required_identity(
    value: str,
    *,
    field_name: str,
) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")

    return normalized


def _record_historical_observation(
    historical_by_subject_sequence: dict[
        tuple[str, str, LineageObservationKind, int],
        LineageObservation,
    ],
    observation: LineageObservation,
) -> None:
    key = (
        observation.source_external_id,
        observation.asset_external_id,
        observation.observation_kind,
        observation.sequence,
    )
    existing = historical_by_subject_sequence.get(key)

    if existing is not None and existing != observation:
        raise ValueError(
            "Conflicting lineage observations share the same "
            "subject sequence."
        )

    historical_by_subject_sequence[key] = observation


def _observation_sort_key(
    observation: LineageObservation,
) -> tuple[str, str, str, int, str]:
    return (
        observation.source_external_id,
        observation.asset_external_id,
        observation.observation_kind,
        observation.sequence,
        observation.revision_external_id,
    )