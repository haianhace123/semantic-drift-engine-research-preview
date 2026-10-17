from __future__ import annotations

import json
import sys
import types
from uuid import uuid4

import pytest

from src.services.risk_confidence_exposure_service import (
    RISK_VERSION_V2,
    RISK_VERSION_V3,
    RiskConfidenceSample,
    load_scoped_assessments,
    summarize_confidence_exposure,
)
from scripts.evaluate_risk_confidence_exposure import main, validate_execution


def row(score, contribution, version=RISK_VERSION_V2):
    return RiskConfidenceSample(version, score, contribution)


def test_counterfactual_uses_persisted_component_and_canonical_thresholds():
    report = summarize_confidence_exposure(
        [row(.65, .09), row(.85, .09), row(.399, .09), row(.50, .0)],
        risk_version=RISK_VERSION_V2,
    )
    assert report["assessments_evaluated"] == 4
    assert report["severity_changes_if_contribution_removed"] == 2
    assert report["severity_transitions"] == {"critical_to_high": 1, "high_to_medium": 1}
    assert report["baseline_severity_counts"] == {"critical": 1, "high": 1, "low": 1, "medium": 1}
    assert report["counterfactual_severity_counts"] == {"high": 1, "low": 1, "medium": 2}
    assert report["mean_confidence_contribution_points"] == 6.75
    assert report["p95_confidence_contribution_points"] == 9.0
    assert report["max_confidence_contribution_points"] == 9.0


def test_shadow_version_stays_independent():
    report = summarize_confidence_exposure(
        [row(.49, .08, RISK_VERSION_V3)], risk_version=RISK_VERSION_V3, truncated=True,
    )
    assert report["truncated"] is True
    assert report["risk_version"] == RISK_VERSION_V3
    assert report["severity_transitions"] == {}


def test_empty_sample_is_not_reported_as_zero_effect():
    report = summarize_confidence_exposure([], risk_version=RISK_VERSION_V2)
    assert report["assessments_evaluated"] == 0
    assert report["severity_change_rate"] is None
    assert report["mean_confidence_contribution_points"] is None


@pytest.mark.parametrize("samples", [
    [row(float("nan"), .05)],
    [row(float("inf"), .05)],
    [row(-.1, .05)],
    [row(.7, -.01)],
    [row(.7, float("inf"))],
    [row(.7, .101)],
    [row(.01, .09)],
    [row(.7, .09, RISK_VERSION_V3)],
])
def test_bad_rows_fail_closed(samples):
    with pytest.raises(ValueError):
        summarize_confidence_exposure(samples, risk_version=RISK_VERSION_V2)


def test_unknown_version_rejected():
    with pytest.raises(ValueError):
        summarize_confidence_exposure([], risk_version="unversioned")


def test_plan_has_no_database_reads(monkeypatch, capsys):
    monkeypatch.delenv("SDE_RISK_CONFIDENCE_AUDIT_ALLOW_DB_READ", raising=False)
    assert main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "plan"
    assert report["provider_calls"] == 0
    assert report["writes"] is False


def test_execute_requires_tenant_and_opt_in(monkeypatch):
    monkeypatch.delenv("SDE_RISK_CONFIDENCE_AUDIT_ALLOW_DB_READ", raising=False)
    with pytest.raises(ValueError, match="opt-in"):
        main(["--execute", "--organization-id", str(uuid4())])
    with pytest.raises(ValueError, match="organization-id"):
        validate_execution(execute=True, organization_id=None,
                           environ={"SDE_RISK_CONFIDENCE_AUDIT_ALLOW_DB_READ": "1"})
    with pytest.raises(ValueError, match="UUID"):
        validate_execution(execute=True, organization_id="not-uuid",
                           environ={"SDE_RISK_CONFIDENCE_AUDIT_ALLOW_DB_READ": "1"})


@pytest.mark.parametrize("limit", [0, -1, 5001])
def test_cli_rejects_unbounded_reads(limit):
    with pytest.raises(SystemExit):
        main(["--limit", str(limit)])


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, rows_by_version):
        self.read_only = False
        self.rollbacks = 0
        self.queries = []
        self.rows_by_version = rows_by_version

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def connection(self):
        return self

    def exec_driver_sql(self, sql):
        assert sql == "SET TRANSACTION READ ONLY"
        self.read_only = True

    def execute(self, stmt):
        assert self.read_only, "DB read attempted before transaction set read-only"
        compiled = stmt.compile()
        values = list(compiled.params.values())
        versions = set(values) & {RISK_VERSION_V2, RISK_VERSION_V3}
        assert len(versions) == 1
        version = next(iter(versions))
        self.queries.append(stmt)
        return FakeResult(self.rows_by_version.get(version, []))

    def rollback(self):
        self.rollbacks += 1


def test_db_audit_enforces_read_only_and_tenant_scope(monkeypatch, capsys):
    from sqlalchemy import Column, DateTime, Float, MetaData, String, Table
    from sqlalchemy.dialects.postgresql import UUID
    columns = Table("risk_assessments", MetaData(),
        Column("id", UUID), Column("organization_id", UUID),
        Column("risk_version", String), Column("assessed_at", DateTime),
        Column("score", Float), Column("confidence_contribution", Float),
    ).c
    fake_model = types.ModuleType("src.models.risk_assessment")
    fake_model.RiskAssessment = columns
    monkeypatch.setitem(sys.modules, "src.models.risk_assessment", fake_model)
    org_id = uuid4()
    session = FakeSession({
        RISK_VERSION_V2: [(0.65, 0.09), (0.6, 0.09)],
        RISK_VERSION_V3: [(0.85, 0.09)],
    })
    module = types.ModuleType("src.core.database")
    module.SessionLocal = lambda: session
    monkeypatch.setitem(sys.modules, "src.core.database", module)
    monkeypatch.setenv("SDE_RISK_CONFIDENCE_AUDIT_ALLOW_DB_READ", "1")

    assert main(["--execute", "--organization-id", str(org_id), "--limit", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert session.rollbacks == 1
    assert len(session.queries) == 2
    for stmt in session.queries:
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert org_id in stmt.compile().params.values()
        assert "ORDER BY" in sql
        assert "LIMIT 2" in sql
    assert report["versions"][0]["truncated"] is True
    assert report["versions"][0]["assessments_evaluated"] == 1
    assert report["versions"][1]["truncated"] is False
    assert report["versions"][1]["severity_transitions"] == {"critical_to_high": 1}
    assert "event_id" not in json.dumps(report)


def test_query_rejects_invalid_scope_before_db_access():
    class NoQuery:
        def execute(self, stmt):
            raise AssertionError("no query should happen")
    for kwargs in (
        dict(organization_id="not uuid", risk_version=RISK_VERSION_V2, limit=1),
        dict(organization_id=uuid4(), risk_version="garbage", limit=1),
        dict(organization_id=uuid4(), risk_version=RISK_VERSION_V2, limit=5001),
    ):
        with pytest.raises(ValueError):
            load_scoped_assessments(NoQuery(), **kwargs)
