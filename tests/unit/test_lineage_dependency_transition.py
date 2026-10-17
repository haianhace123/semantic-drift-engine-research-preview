from __future__ import annotations

from dataclasses import replace

import pytest

from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
    compare_materialization_dependencies,
)


def dep(asset: str, revision: str, kind: str = "derived_from"):
    return LineageRevisionReference(
        asset_external_id=asset,
        revision_external_id=revision,
        dependency_type=kind,
    )


def materialization(sequence: int, dependencies=()):
    return LineageObservation(
        observation_kind="materialization",
        source_external_id="tenant-data-source",
        snapshot_external_id=f"snapshot-{sequence}",
        asset_external_id="search-index",
        asset_kind="index",
        revision_external_id=f"index-{sequence}",
        sequence=sequence,
        dependencies=tuple(dependencies),
    )


def test_diff_tracks_exact_revision_and_type_without_collapsing():
    before = materialization(1, (
        dep("policy", "v1", "indexed_from"),
        dep("manual", "v1", "cites"),
    ))
    after = materialization(2, (
        dep("manual", "v1", "cites"),
        dep("policy", "v2", "indexed_from"),
    ))

    transition = compare_materialization_dependencies(before, after)

    assert transition.added == (dep("policy", "v2", "indexed_from"),)
    assert transition.removed == (dep("policy", "v1", "indexed_from"),)
    assert transition.unchanged == (dep("manual", "v1", "cites"),)
    assert transition.before is before
    assert transition.after is after


def test_empty_new_revision_retires_all_reported_dependencies():
    old = materialization(1, (dep("policy", "v1"),))
    new = materialization(2)
    comparison = compare_materialization_dependencies(old, new)
    assert comparison.removed == old.dependencies
    assert comparison.added == ()
    assert comparison.unchanged == ()


def test_order_does_not_create_false_changes():
    old = materialization(1, (dep("b", "2"), dep("a", "1")))
    new = materialization(2, tuple(reversed(old.dependencies)))
    comparison = compare_materialization_dependencies(old, new)
    assert comparison.added == comparison.removed == ()
    assert comparison.unchanged == (dep("a", "1"), dep("b", "2"))


@pytest.mark.parametrize("change", [
    {"source_external_id": "different-source"},
    {"asset_external_id": "different-asset"},
    {"asset_kind": "agent"},
    {"sequence": 1},
    {"sequence": 0},
    {"observation_kind": "consumption", "dependencies": (dep("x", "1"),)},
])
def test_rejects_invalid_subject_or_order(change):
    old = materialization(1, (dep("x", "1"),))
    new = replace(materialization(2, (dep("x", "1"),)), **change)
    with pytest.raises(ValueError, match="ordered materializations"):
        compare_materialization_dependencies(old, new)


def test_rejects_consumption_as_from_state():
    old = replace(
        materialization(1, (dep("x", "1"),)),
        observation_kind="consumption",
    )
    with pytest.raises(ValueError, match="ordered materializations"):
        compare_materialization_dependencies(old, materialization(2))
