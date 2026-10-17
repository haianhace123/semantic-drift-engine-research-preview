from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest

from src.domain.lineage_invalidation import (
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.services import temporal_lineage_invalidation_service as service


POLICY = LineageInvalidationPolicyIdentity(
    policy_external_id="sde-default-lineage-invalidation",
    revision=1,
)


class _Query:
    def __init__(self, event):
        self._event = event

    def filter(self, *args):
        return self

    def one_or_none(self):
        return self._event


class _Session:
    def __init__(self, event):
        self._event = event

    def query(self, model):
        return _Query(self._event)


def _event(
    *,
    analysis_status: str = "completed",
    vector_analysis_status: str = "completed",
    changes_count: int = 1,
    semantic_delta_json=None,
):
    if semantic_delta_json is None:
        semantic_delta_json = {"semantic_analysis": {
            "changes": [{"before": "30", "after": "7"}] if changes_count else [],
        }}
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        old_snapshot_id=uuid.uuid4(),
        new_snapshot_id=uuid.uuid4(),
        analysis_status=analysis_status,
        vector_analysis_status=vector_analysis_status,
        changes_count=changes_count,
        semantic_delta_json=semantic_delta_json,
        risk_level="low" if changes_count == 0 else "medium",
    )


def test_analysis_uses_old_snapshot_roots_and_persists_each_root(
    monkeypatch,
):
    event = _event()
    db = _Session(event)

    roots = (
        LineageRevisionIdentity(
            source_external_id="source",
            asset_external_id="chunk-a",
            revision_external_id="revision-a",
        ),
        LineageRevisionIdentity(
            source_external_id="source",
            asset_external_id="chunk-b",
            revision_external_id="revision-b",
        ),
    )

    resolved = []
    analyzed = []
    provenance_persisted = []
    decision_ids = {
        root.asset_external_id: uuid.uuid4()
        for root in roots
    }

    def fake_resolve(
        db_arg,
        *,
        organization_id,
        page_snapshot_id,
    ):
        resolved.append(
            (
                db_arg,
                organization_id,
                page_snapshot_id,
            )
        )
        return roots

    def fake_analyze(
        db_arg,
        *,
        organization_id,
        root,
        policy_identity,
    ):
        analyzed.append(
            (
                db_arg,
                organization_id,
                root,
                policy_identity,
            )
        )
        return SimpleNamespace(
            label=f"analysis:{root.asset_external_id}",
            decisions=(
                SimpleNamespace(
                    record=SimpleNamespace(
                        id=decision_ids[root.asset_external_id],
                    ),
                ),
            ),
        )

    def fake_persist_provenance(
        db_arg,
        *,
        organization_id,
        semantic_drift_event_id,
        decision_id,
    ):
        provenance_persisted.append(
            (
                db_arg,
                organization_id,
                semantic_drift_event_id,
                decision_id,
            )
        )
    monkeypatch.setattr(
        service,
        "resolve_temporal_lineage_roots",
        fake_resolve,
    )
    monkeypatch.setattr(
        service,
        "analyze_and_persist_lineage_invalidation_decisions",
        fake_analyze,
    )
    monkeypatch.setattr(
        service,
        "persist_temporal_lineage_invalidation_decision_provenance",
        fake_persist_provenance,
    )

    result = service.analyze_temporal_lineage_invalidation(
        db,
        organization_id=event.organization_id,
        event_id=event.id,
        policy_identity=POLICY,
    )

    assert resolved == [
        (
            db,
            event.organization_id,
            event.old_snapshot_id,
        )
    ]
    assert [
        call[2]
        for call in analyzed
    ] == list(roots)
    assert all(
        call[3] == POLICY
        for call in analyzed
    )
    assert provenance_persisted == [
        (
            db,
            event.organization_id,
            event.id,
            decision_ids["chunk-a"],
        ),
        (
            db,
            event.organization_id,
            event.id,
            decision_ids["chunk-b"],
        ),
    ]
    assert result.event_id == event.id
    assert result.old_snapshot_id == event.old_snapshot_id
    assert result.new_snapshot_id == event.new_snapshot_id
    assert result.roots == roots
    assert tuple(
        analysis.label
        for analysis in result.analyses
    ) == (
        "analysis:chunk-a",
        "analysis:chunk-b",
    )


def test_analysis_with_no_old_snapshot_roots_is_noop(
    monkeypatch,
):
    event = _event()
    db = _Session(event)

    monkeypatch.setattr(
        service,
        "resolve_temporal_lineage_roots",
        lambda *args, **kwargs: (),
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError(
            "Invalidation analysis must not run without lineage roots."
        )

    monkeypatch.setattr(
        service,
        "analyze_and_persist_lineage_invalidation_decisions",
        fail_if_called,
    )

    result = service.analyze_temporal_lineage_invalidation(
        db,
        organization_id=event.organization_id,
        event_id=event.id,
        policy_identity=POLICY,
    )

    assert result.roots == ()
    assert result.analyses == ()


@pytest.mark.parametrize(
    (
        "analysis_status",
        "vector_analysis_status",
    ),
    (
        ("pending", "completed"),
        ("failed", "completed"),
        ("completed", "pending"),
        ("completed", "processing"),
        ("completed", "failed"),
    ),
)
def test_analysis_fails_closed_when_event_evidence_is_not_ready(
    monkeypatch,
    analysis_status,
    vector_analysis_status,
):
    event = _event(
        analysis_status=analysis_status,
        vector_analysis_status=vector_analysis_status,
    )
    db = _Session(event)

    def fail_if_called(*args, **kwargs):
        raise AssertionError(
            "Lineage roots must not be resolved before evidence is ready."
        )

    monkeypatch.setattr(
        service,
        "resolve_temporal_lineage_roots",
        fail_if_called,
    )

    with pytest.raises(
        service.TemporalLineageInvalidationNotReadyError
    ):
        service.analyze_temporal_lineage_invalidation(
            db,
            organization_id=event.organization_id,
            event_id=event.id,
            policy_identity=POLICY,
        )


def test_analysis_rejects_event_outside_tenant_scope():
    db = _Session(None)

    with pytest.raises(
        service.TemporalLineageInvalidationEventNotFoundError
    ):
        service.analyze_temporal_lineage_invalidation(
            db,
            organization_id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            policy_identity=POLICY,
        )

def test_nonmaterial_edit_never_resolves_roots_or_persists_decisions(monkeypatch):
    event = _event(changes_count=0)
    db = _Session(event)

    def forbidden(*args, **kwargs):
        raise AssertionError("Nonmaterial changes must not produce invalidation.")

    monkeypatch.setattr(service, "resolve_temporal_lineage_roots", forbidden)
    monkeypatch.setattr(service, "analyze_and_persist_lineage_invalidation_decisions", forbidden)
    monkeypatch.setattr(service, "persist_temporal_lineage_invalidation_decision_provenance", forbidden)

    result = service.analyze_temporal_lineage_invalidation(
        db, organization_id=event.organization_id,
        event_id=event.id, policy_identity=POLICY,
    )
    assert result.roots == ()
    assert result.analyses == ()


@pytest.mark.parametrize("count,payload", [
    (None, {"changes": []}),
    (-1, {"changes": []}),
    (True, {"changes": [{}]}),
    (0, {"changes": [{"before": "30", "after": "7"}]}),
    (1, {"changes": []}),
    (0, {"semantic_analysis": {"changes": [{"before": "30"}]}}),
    (1, {"semantic_analysis": {"changes": []}}),
    (0, {}),
    (1, {"changes": "changed"}),
    (0, {"changes": ["not-a-change-object"]}),
])
def test_incoherent_completed_materiality_fails_without_lineage_side_effects(
    monkeypatch, count, payload,
):
    event = _event(changes_count=count, semantic_delta_json=payload)
    db = _Session(event)

    def forbidden(*args, **kwargs):
        raise AssertionError("Incoherent evidence must not inspect or write lineage.")

    monkeypatch.setattr(service, "resolve_temporal_lineage_roots", forbidden)
    monkeypatch.setattr(service, "analyze_and_persist_lineage_invalidation_decisions", forbidden)
    with pytest.raises(service.TemporalLineageInvalidationEvidenceConflictError):
        service.analyze_temporal_lineage_invalidation(
            db, organization_id=event.organization_id,
            event_id=event.id, policy_identity=POLICY,
        )


def test_materiality_accepts_legacy_root_level_changes(monkeypatch):
    event = _event(
        changes_count=1,
        semantic_delta_json={"changes": [{"before": "30", "after": "7"}]},
    )
    monkeypatch.setattr(service, "resolve_temporal_lineage_roots", lambda *a, **kw: ())
    result = service.analyze_temporal_lineage_invalidation(
        _Session(event), organization_id=event.organization_id,
        event_id=event.id, policy_identity=POLICY,
    )
    assert result.roots == ()


def test_equivalent_edit_with_high_risk_is_not_silently_cleared(monkeypatch):
    event = _event(changes_count=0)
    event.risk_level = "high"
    monkeypatch.setattr(
        service, "resolve_temporal_lineage_roots",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Incoherent risk must not inspect lineage.")
        ),
    )
    with pytest.raises(service.TemporalLineageInvalidationEvidenceConflictError):
        service.analyze_temporal_lineage_invalidation(
            _Session(event), organization_id=event.organization_id,
            event_id=event.id, policy_identity=POLICY,
        )
