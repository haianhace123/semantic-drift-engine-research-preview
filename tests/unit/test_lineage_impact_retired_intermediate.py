from __future__ import annotations

import uuid
from types import SimpleNamespace

from src.domain.lineage_revision import LineageRevisionIdentity
from src.services import lineage_impact_service


def _rev(asset: str, version: str) -> LineageRevisionIdentity:
    return LineageRevisionIdentity(
        source_external_id="source-a",
        asset_external_id=asset,
        revision_external_id=version,
    )


def _observation(revision: LineageRevisionIdentity):
    return SimpleNamespace(
        source_external_id=revision.source_external_id,
        asset_external_id=revision.asset_external_id,
        revision_external_id=revision.revision_external_id,
    )


def test_retired_intermediate_can_reach_current_stale_consumer(monkeypatch):
    policy_old = _rev("policy", "v1")
    index_old = _rev("index", "v1")
    agent_old = _rev("agent", "v1")
    historical = {
        policy_old: (_observation(index_old),),
        index_old: (_observation(agent_old),),
        agent_old: (),
    }
    current = {
        policy_old: (),  # index now points to policy v2
        index_old: (_observation(agent_old),),
        agent_old: (),
    }
    calls = []

    def fake_batch(db, *, organization_id, revisions, current_only):
        calls.append((revisions, current_only))
        mapping = current if current_only else historical
        return {revision: mapping[revision] for revision in revisions}

    monkeypatch.setattr(
        lineage_impact_service,
        "load_exact_revision_dependents_batch",
        fake_batch,
    )
    analysis = lineage_impact_service.analyze_lineage_impact(
        object(), organization_id=uuid.uuid4(), root=policy_old,
    )
    assert analysis.impacted_revisions == (agent_old,)
    assert analysis.paths[0].revisions == (policy_old, index_old, agent_old)
    assert not any(path.target == index_old for path in analysis.paths)
    assert len(calls) == 6  # two batched reads per BFS level


def test_no_current_descendant_of_retired_intermediate(monkeypatch):
    policy_old = _rev("policy", "v1")
    index_old = _rev("index", "v1")
    agent_old = _rev("agent", "v1")
    historical = {
        policy_old: (_observation(index_old),),
        index_old: (_observation(agent_old),),
        agent_old: (),
    }

    def fake_batch(db, *, organization_id, revisions, current_only):
        if current_only:
            return {revision: () for revision in revisions}
        return {revision: historical[revision] for revision in revisions}

    monkeypatch.setattr(
        lineage_impact_service,
        "load_exact_revision_dependents_batch",
        fake_batch,
    )
    result = lineage_impact_service.analyze_lineage_impact(
        object(), organization_id=uuid.uuid4(), root=policy_old,
    )
    assert result.impacted_revisions == ()
    assert result.paths == ()


def test_retired_cycle_does_not_revisit_root(monkeypatch):
    root = _rev("policy", "v1")
    old = _rev("index", "v1")
    current = _rev("agent", "v1")
    historical = {
        root: (_observation(old),),
        old: (_observation(root), _observation(current)),
        current: (_observation(old),),
    }

    def fake_batch(db, *, organization_id, revisions, current_only):
        if current_only:
            return {
                revision: (_observation(current),) if revision == old else ()
                for revision in revisions
            }
        return {revision: historical[revision] for revision in revisions}

    monkeypatch.setattr(
        lineage_impact_service,
        "load_exact_revision_dependents_batch",
        fake_batch,
    )
    result = lineage_impact_service.analyze_lineage_impact(
        object(), organization_id=uuid.uuid4(), root=root,
    )
    assert result.impacted_revisions == (current,)
    assert result.paths[0].revisions == (root, old, current)
