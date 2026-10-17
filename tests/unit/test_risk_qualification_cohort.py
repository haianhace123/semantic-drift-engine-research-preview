"""Event-unit boundaries for Risk scoring qualification (no DB access)."""
from __future__ import annotations

import json
import sys
import types
from uuid import uuid4

import pytest

from src.services.risk_version import RISK_VERSION_V2, RISK_VERSION_V3
from src.services.risk_qualification_cohort_service import (
    QualificationAssessment, load_qualification_cohort,
    summarize_risk_qualification,
)
from scripts.evaluate_risk_qualification_cohort import main

ORG = uuid4()
OTHER = uuid4()


def sample(event_id=None, *, version=RISK_VERSION_V2, org=ORG, score=.43, confidence=.09, signal=True, previous=False, topology=0):
    return QualificationAssessment(org, event_id, version, score, confidence, signal, previous, topology)


def test_plan_requires_no_secrets_or_db(capsys, monkeypatch):
    monkeypatch.delenv("SDE_RISK_QUALIFICATION_ALLOW_DB_READ", raising=False)
    assert main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "plan"
    assert report["provider_calls"] == 0
    assert report["read_only"] is True


def test_exec_denied_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("SDE_RISK_QUALIFICATION_ALLOW_DB_READ", raising=False)
    with pytest.raises(SystemExit):
        main(["--execute", "--organization-id", str(ORG)])


def test_invalid_organization_denied_before_db(monkeypatch):
    monkeypatch.setenv("SDE_RISK_QUALIFICATION_ALLOW_DB_READ", "1")
    for oid in ("", "no-uuid"):
        with pytest.raises(SystemExit):
            main(["--execute", "--organization-id", oid])


@pytest.mark.parametrize("limit", [0, -1, 5001])
def test_bounded_limit(limit):
    with pytest.raises(SystemExit):
        main(["--limit", str(limit)])


def test_plan_refuses_review_identifiers():
    with pytest.raises(SystemExit):
        main(["--include-review-event-ids"])


def test_empty_cohort_is_not_successful_qualification():
    report = summarize_risk_qualification([], [], organization_id=ORG, truncated=False)
    assert report["qualification"] == "no_baseline_evidence"
    assert report["pair_coverage_of_linked"] is None
    assert report["confidence_band_flip_rate"] is None
    assert report["model_accuracy_evaluated"] is False


def test_event_pairing_and_topology_baseline_are_distinct():
    a, b, c, d = [uuid4() for _ in range(4)]
    baseline = [
        sample(a, score=.43, signal=True, previous=False),
        sample(b, score=.39, signal=False, previous=False),
        sample(c, score=.5, signal=True, previous=True, topology=.05),
        sample(d, score=.42, signal=True, previous=True, topology=0),
    ]
    paired = [sample(a, version=RISK_VERSION_V3), sample(c, version=RISK_VERSION_V3, score=.55, previous=True, topology=.05)]
    report = summarize_risk_qualification(baseline, paired, organization_id=ORG, truncated=False)
    assert report["linked_unique_baseline_events"] == 4
    assert report["paired_shadow_events"] == 2
    assert report["missing_shadow_events"] == 2
    assert report["pair_coverage_of_linked"] == .5
    assert report["topology_comparable"] == 2
    assert report["topology_baseline_only"] == 1
    assert report["topology_unavailable"] == 1
    assert report["topology_positive_contribution"] == 1
    assert report["confidence_band_flips"] == 2
    assert report["confidence_transitions"] == {"medium_to_low": 2}
    assert report["paired_version_score_difference"] == 1
    assert report["qualification"] == "incomplete_event_pairing"
    assert "review_event_ids" not in report


def test_fully_paired_still_not_ground_truth():
    event = uuid4()
    report = summarize_risk_qualification(
        [sample(event)], [sample(event, version=RISK_VERSION_V3)], organization_id=ORG, truncated=False,
    )
    assert report["qualification"] == "paired_cohort_available_not_ground_truth"
    assert report["independent_severity_labels"] == 0
    assert report["model_accuracy_evaluated"] is False


def test_truncation_never_implies_population_coverage():
    event = uuid4()
    report = summarize_risk_qualification(
        [sample(event)], [sample(event, version=RISK_VERSION_V3)], organization_id=ORG, truncated=True,
    )
    assert report["qualification"] == "bounded_sample_only"
    assert report["truncated"] is True


def test_unlinked_baseline_is_counted_not_merged():
    a = uuid4()
    report = summarize_risk_qualification([sample(None), sample(a)], [], organization_id=ORG, truncated=False)
    assert report["unlinked_baseline_assessments"] == 1
    assert report["linked_unique_baseline_events"] == 1
    assert report["missing_shadow_events"] == 1


@pytest.mark.parametrize("baseline,candidate", [
    ([sample(uuid4(), org=OTHER)], []),
    ([sample(uuid4(), version=RISK_VERSION_V3)], []),
    ([sample(uuid4(), score=float('nan'))], []),
    ([sample(uuid4(), confidence=.11)], []),
    ([sample(uuid4(), score=.01, confidence=.09)], []),
    ([sample(uuid4(), previous=False, topology=.01)], []),
    ([sample(uuid4(), signal=False, previous=True)], []),
    ([sample(uuid4(), signal=1)], []),
])
def test_invalid_baseline_fails_closed(baseline, candidate):
    with pytest.raises(ValueError):
        summarize_risk_qualification(baseline, candidate, organization_id=ORG, truncated=False)


def test_duplicate_baseline_event_rejected():
    eid = uuid4()
    with pytest.raises(ValueError, match="duplicate"):
        summarize_risk_qualification([sample(eid), sample(eid)], [], organization_id=ORG, truncated=False)


def test_duplicate_shadow_event_rejected():
    eid = uuid4()
    with pytest.raises(ValueError, match="duplicate"):
        summarize_risk_qualification([sample(eid)], [sample(eid, version=RISK_VERSION_V3)] * 2, organization_id=ORG, truncated=False)


def test_shadow_from_other_tenant_or_unknown_event_rejected():
    eid = uuid4()
    for candidate in (sample(eid, version=RISK_VERSION_V3, org=OTHER), sample(uuid4(), version=RISK_VERSION_V3)):
        with pytest.raises(ValueError):
            summarize_risk_qualification([sample(eid)], [candidate], organization_id=ORG, truncated=False)


def test_review_ids_opt_in_and_bounded():
    baseline = [sample(uuid4()) for _ in range(70)]
    report = summarize_risk_qualification(baseline, [], organization_id=ORG, truncated=False, include_review_event_ids=True)
    assert len(report["review_event_ids"]) == 50
    assert report["review_event_ids"][0] == str(baseline[0].event_id)
    assert "review_event_ids" not in summarize_risk_qualification(baseline, [], organization_id=ORG, truncated=False)


class Rows:
    def __init__(self, rows): self.rows = rows
    def all(self): return self.rows


class Session:
    def __init__(self, baseline, shadow):
        self.baseline = baseline; self.shadow = shadow; self.statements = []
    def execute(self, statement):
        self.statements.append(statement)
        p = list(statement.compile().params.values())
        if RISK_VERSION_V2 in p: return Rows(self.baseline)
        if RISK_VERSION_V3 in p: return Rows(self.shadow)
        raise AssertionError("bad version query")


def fake_model(monkeypatch):
    from sqlalchemy import Column, Float, MetaData, Table, Boolean, DateTime, String
    from sqlalchemy.dialects.postgresql import UUID
    columns = Table("risk_assessments", MetaData(),
        Column("id", UUID), Column("organization_id", UUID),
        Column("semantic_drift_event_id", UUID), Column("risk_version", String),
        Column("score", Float), Column("confidence_contribution", Float),
        Column("has_topology_signal", Boolean), Column("has_previous_topology_state", Boolean),
        Column("topology_contribution", Float), Column("assessed_at", DateTime)).c
    fake = types.ModuleType("src.models.risk_assessment")
    fake.RiskAssessment = columns
    monkeypatch.setitem(sys.modules, "src.models.risk_assessment", fake)
    events = Table("semantic_drift_events", MetaData(),
        Column("id", UUID), Column("organization_id", UUID))
    event_model = types.ModuleType("src.models.semantic_drift_event")
    class FakeEvent:
        id = events.c.id
        organization_id = events.c.organization_id
        __table__ = events
    event_model.SemanticDriftEvent = FakeEvent
    monkeypatch.setitem(sys.modules, "src.models.semantic_drift_event", event_model)


def db_tuple(eid, version=RISK_VERSION_V2, org=ORG):
    return (org, eid, version, .43, .09, True, False, 0., org if eid else None)


def test_cross_tenant_event_reference_rejected_before_pairing(monkeypatch):
    fake_model(monkeypatch)
    eid = uuid4()
    session = Session([db_tuple(eid)[:-1] + (OTHER,)], [])
    with pytest.raises(ValueError, match="cross-tenant"):
        load_qualification_cohort(session, organization_id=ORG, limit=5)
    assert len(session.statements) == 1


def test_missing_event_reference_rejected_before_pairing(monkeypatch):
    fake_model(monkeypatch)
    eid = uuid4()
    session = Session([db_tuple(eid)[:-1] + (None,)], [])
    with pytest.raises(ValueError, match="missing"):
        load_qualification_cohort(session, organization_id=ORG, limit=5)


def test_query_bounds_each_stage_to_tenant_and_event_ids(monkeypatch):
    fake_model(monkeypatch)
    eid1, eid2 = uuid4(), uuid4()
    session = Session([db_tuple(eid1), db_tuple(eid2)], [db_tuple(eid1, RISK_VERSION_V3)])
    base, shadow, truncated = load_qualification_cohort(session, organization_id=ORG, limit=2)
    assert len(base) == 2 and len(shadow) == 1 and not truncated
    assert len(session.statements) == 2
    for stmt in session.statements:
        assert ORG in stmt.compile().params.values()
    assert eid1.hex in str(session.statements[1].compile(compile_kwargs={"literal_binds": True}))
    assert eid2.hex in str(session.statements[1].compile(compile_kwargs={"literal_binds": True}))


def test_unlinked_rows_do_not_issue_shadow_query(monkeypatch):
    fake_model(monkeypatch)
    session = Session([db_tuple(None)], [])
    base, shadow, truncated = load_qualification_cohort(session, organization_id=ORG, limit=5)
    assert len(base) == 1 and not shadow and len(session.statements) == 1


def test_query_rejects_duplicate_before_shadow_query(monkeypatch):
    fake_model(monkeypatch)
    eid = uuid4()
    session = Session([db_tuple(eid), db_tuple(eid)], [])
    with pytest.raises(ValueError, match="duplicate"):
        load_qualification_cohort(session, organization_id=ORG, limit=3)
    assert len(session.statements) == 1


def test_query_rejects_cross_scope_and_unbounded_reads():
    class Never:
        def execute(self, stmt): raise AssertionError("no db query allowed")
    for org, limit in (("not-uuid", 1), (ORG, 0), (ORG, 5001), (ORG, True)):
        with pytest.raises(ValueError):
            load_qualification_cohort(Never(), organization_id=org, limit=limit)
