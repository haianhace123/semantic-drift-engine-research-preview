from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BenchmarkSplit = Literal[
    "calibration",
    "evaluation",
]

BenchmarkDifficulty = Literal[
    "easy",
    "medium",
    "hard",
]


@dataclass(frozen=True)
class NamedGraphState:
    entities: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class TopologyBenchmarkCase:
    id: str
    pair_id: str
    split: BenchmarkSplit
    category: str
    domain: str
    difficulty: BenchmarkDifficulty
    consequential_change: bool
    critical_change: bool
    before: NamedGraphState
    after: NamedGraphState


def _load_graph_state(
    value: object,
    *,
    case_id: str,
    field_name: str,
) -> NamedGraphState:
    if not isinstance(value, dict):
        raise ValueError(
            f"{case_id}: {field_name} must be an object."
        )

    raw_entities = value.get("entities")
    raw_edges = value.get("edges")

    if not isinstance(raw_entities, list):
        raise ValueError(
            f"{case_id}: {field_name}.entities must be a list."
        )

    entities = tuple(raw_entities)

    if (
        not entities
        or any(
            not isinstance(entity, str)
            or not entity.strip()
            for entity in entities
        )
    ):
        raise ValueError(
            f"{case_id}: {field_name}.entities must contain "
            "non-empty strings."
        )

    if len(set(entities)) != len(entities):
        raise ValueError(
            f"{case_id}: {field_name}.entities must be unique."
        )

    if not isinstance(raw_edges, list):
        raise ValueError(
            f"{case_id}: {field_name}.edges must be a list."
        )

    edges: list[tuple[str, str]] = []

    for raw_edge in raw_edges:
        if (
            not isinstance(raw_edge, list)
            or len(raw_edge) != 2
            or not all(
                isinstance(entity, str)
                and entity.strip()
                for entity in raw_edge
            )
        ):
            raise ValueError(
                f"{case_id}: every {field_name}.edges entry "
                "must contain two non-empty entity names."
            )

        source, target = raw_edge

        if source == target:
            raise ValueError(
                f"{case_id}: {field_name} contains a self-loop."
            )

        if (
            source not in entities
            or target not in entities
        ):
            raise ValueError(
                f"{case_id}: {field_name} edge references an "
                "unknown entity."
            )

        edges.append(
            (
                source,
                target,
            )
        )

    if len(set(edges)) != len(edges):
        raise ValueError(
            f"{case_id}: {field_name}.edges must be unique."
        )

    return NamedGraphState(
        entities=entities,
        edges=tuple(edges),
    )


def _load_case(
    value: object,
) -> TopologyBenchmarkCase:
    if not isinstance(value, dict):
        raise ValueError(
            "Topology benchmark cases must be objects."
        )

    case_id = value.get("id")

    if (
        not isinstance(case_id, str)
        or not case_id.strip()
    ):
        raise ValueError(
            "Topology benchmark case id must be a non-empty string."
        )

    pair_id = value.get("pair_id")
    split = value.get("split")
    category = value.get("category")
    domain = value.get("domain")
    difficulty = value.get("difficulty")
    consequential_change = value.get(
        "consequential_change"
    )
    critical_change = value.get(
        "critical_change"
    )

    if (
        not isinstance(pair_id, str)
        or not pair_id.strip()
    ):
        raise ValueError(
            f"{case_id}: pair_id must be a non-empty string."
        )

    if split not in {
        "calibration",
        "evaluation",
    }:
        raise ValueError(
            f"{case_id}: invalid split."
        )

    if (
        not isinstance(category, str)
        or not category.strip()
    ):
        raise ValueError(
            f"{case_id}: category must be a non-empty string."
        )

    if (
        not isinstance(domain, str)
        or not domain.strip()
    ):
        raise ValueError(
            f"{case_id}: domain must be a non-empty string."
        )

    if difficulty not in {
        "easy",
        "medium",
        "hard",
    }:
        raise ValueError(
            f"{case_id}: invalid difficulty."
        )

    if not isinstance(
        consequential_change,
        bool,
    ):
        raise ValueError(
            f"{case_id}: consequential_change must be boolean."
        )

    if not isinstance(
        critical_change,
        bool,
    ):
        raise ValueError(
            f"{case_id}: critical_change must be boolean."
        )

    if (
        critical_change
        and not consequential_change
    ):
        raise ValueError(
            f"{case_id}: critical change must also be consequential."
        )

    return TopologyBenchmarkCase(
        id=case_id,
        pair_id=pair_id,
        split=split,
        category=category,
        domain=domain,
        difficulty=difficulty,
        consequential_change=consequential_change,
        critical_change=critical_change,
        before=_load_graph_state(
            value.get("before"),
            case_id=case_id,
            field_name="before",
        ),
        after=_load_graph_state(
            value.get("after"),
            case_id=case_id,
            field_name="after",
        ),
    )


def _validate_pairs(
    cases: tuple[TopologyBenchmarkCase, ...],
) -> None:
    by_pair: dict[
        str,
        list[TopologyBenchmarkCase],
    ] = {}

    for case in cases:
        by_pair.setdefault(
            case.pair_id,
            [],
        ).append(case)

    for pair_id, pair_cases in by_pair.items():
        if len(pair_cases) != 2:
            raise ValueError(
                f"{pair_id}: topology pair must contain exactly "
                "two cases."
            )

        first, second = pair_cases

        if first.split != second.split:
            raise ValueError(
                f"{pair_id}: pair cases must use the same split."
            )

        if first.category != second.category:
            raise ValueError(
                f"{pair_id}: pair cases must use the same category."
            )

        if first.domain != second.domain:
            raise ValueError(
                f"{pair_id}: pair cases must use the same domain."
            )

        if first.before != second.before:
            raise ValueError(
                f"{pair_id}: pair cases must share the same "
                "before graph."
            )

        labels = {
            case.consequential_change
            for case in pair_cases
        }

        if labels != {
            False,
            True,
        }:
            raise ValueError(
                f"{pair_id}: pair must contain one benign control "
                "and one consequential mutation."
            )


def load_topology_cases(
    path: Path | None = None,
) -> tuple[TopologyBenchmarkCase, ...]:
    cases_path = (
        path
        if path is not None
        else Path(__file__).with_name(
            "topology_cases.json"
        )
    )

    raw_cases = json.loads(
        cases_path.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(raw_cases, list):
        raise ValueError(
            "Topology benchmark corpus must be a list."
        )

    cases = tuple(
        _load_case(raw_case)
        for raw_case in raw_cases
    )

    case_ids = [
        case.id
        for case in cases
    ]

    if len(set(case_ids)) != len(case_ids):
        raise ValueError(
            "Topology benchmark case ids must be unique."
        )

    _validate_pairs(cases)

    return cases