from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as Row
from uuid import uuid4

import pytest

from src.services.pipeline_effectiveness_evidence import (
    _elapsed_ms,
    summarize_pipeline_event,
)
from scripts.audit_pipeline_effectiveness import parse_args
from src.services.risk_version import RISK_VERSION_V2


def fixture_rows():
    organization_id = uuid4()
    page_id = uuid4()
    event_id = uuid4()
    old_id = uuid4()
    new_id = uuid4()
    start = datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc)
    event = Row(
        id=event_id,
        organization_id=organization_id,
        monitored_page_id=page_id,
        old_snapshot_id=old_id,
        new_snapshot_id=new_id,
        semantic_delta_json={"changes": ["meaningful"]},
        detected_at=start,
        analysis_status="completed",
        vector_analysis_status="completed",
        risk_scoring_status="completed",
        semantic_similarity=0.2,
        semantic_distance=0.8,
        semantic_magnitude=0.5,
        vector_analyzed_at=start + timedelta(milliseconds=20),
        risk_scored_at=start + timedelta(milliseconds=35),
        risk_score=82.0,
    )
    old = Row(
        id=old_id, organization_id=organization_id,
        monitored_page_id=page_id, content_hash="a" * 64,
        embedding_status="completed", embedding=[1.0],
        embedding_model="test-model", embedding_dimensions=1,
        embedded_at=start,
    )
    new = Row(
        id=new_id, organization_id=organization_id,
        monitored_page_id=page_id, content_hash="b" * 64,
        embedding_status="completed", embedding=[0.0, 1.0],
        embedding_model="test-model", embedding_dimensions=2,
        embedded_at=start,
    )
    risk = Row(
        organization_id=organization_id, semantic_drift_event_id=event_id,
        risk_version=RISK_VERSION_V2, score=0.82,
    )
    return event, old, new, risk


def summarize(event, old, new, risk, **kwargs):
    return summarize_pipeline_event(
        event=event, old_snapshot=old, new_snapshot=new,
        risk_v2_assessment=risk, topology_shift_count=kwargs.get("topology", 0),
        lineage_decision_count=kwargs.get("decisions", 0),
    )


def test_complete_vector_risk_evidence_does_not_claim_lineage():
    result = summarize(*fixture_rows())
    assert result["gates"] == {
        "vector_risk_complete": True, "causal_decisions_observed": False
    }
    assert result["stages"]["topology_shift"] == "not_observed"
    assert result["stages"]["causal_decisions"] == "not_observed"
    assert result["latency_ms"] == {
        "detection_to_vector": 20, "vector_to_risk": 15
    }
    assert "semantic_delta_json" not in result
    assert "raw_content" not in str(result)


def test_pipeline_report_observes_only_linked_topology_and_decisions():
    result = summarize(*fixture_rows(), topology=2, decisions=1)
    assert result["stages"]["topology_shift"] == "observed"
    assert result["stages"]["causal_decisions"] == "observed"
    assert result["measurements"]["topology_shift_records"] == 2
    assert result["gates"]["causal_decisions_observed"] is True


@pytest.mark.parametrize("field", [
    "organization_id", "monitored_page_id", "id", "content_hash"
])
def test_snapshot_tenant_or_identity_mismatch_never_passes(field):
    event, old, new, risk = fixture_rows()
    setattr(old, field, new.content_hash if field == "content_hash" else uuid4())
    result = summarize(event, old, new, risk)
    assert result["gates"]["vector_risk_complete"] is False
    assert result["stages"]["snapshot_pair"] == "inconsistent"


def test_missing_snapshot_never_passes():
    event, old, new, risk = fixture_rows()
    result = summarize(event, old, None, risk)
    assert result["gates"]["vector_risk_complete"] is False
    assert result["stages"]["snapshot_pair"] == "inconsistent"


def test_missing_vector_metrics_report_inconsistency():
    event, old, new, risk = fixture_rows()
    event.semantic_distance = None
    result = summarize(event, old, new, risk)
    assert result["stages"]["vector_analysis"] == "inconsistent"
    assert result["gates"]["vector_risk_complete"] is False


def test_completed_risk_without_v2_assessment_is_not_complete():
    event, old, new, risk = fixture_rows()
    result = summarize(event, old, new, None)
    assert result["stages"]["risk_v2"] == "inconsistent"


def test_risk_assessment_uses_persisted_version_not_event_projection():
    event, old, new, risk = fixture_rows()
    assert RISK_VERSION_V2 == "deterministic_v2"
    assert summarize(event, old, new, risk)["gates"]["vector_risk_complete"] is True

    # Event projection says "v2" but the durable RiskAssessment uses the
    # version identifier from the risk scorer.  Do not accept the alias.
    risk.risk_version = "v2"
    assert summarize(event, old, new, risk)["stages"]["risk_v2"] == "inconsistent"


def test_risk_assessment_other_tenant_is_rejected():
    event, old, new, risk = fixture_rows()
    risk.organization_id = uuid4()
    assert summarize(event, old, new, risk)["stages"]["risk_v2"] == "inconsistent"


def test_risk_v3_alone_does_not_satisfy_v2_gate():
    event, old, new, risk = fixture_rows()
    risk.risk_version = "deterministic_v3"
    assert summarize(event, old, new, risk)["gates"]["vector_risk_complete"] is False


def test_failed_embedding_is_explicit_failure():
    event, old, new, risk = fixture_rows()
    new.embedding_status = "failed"
    assert summarize(event, old, new, risk)["stages"]["embeddings"] == "failed"


def test_missing_embedding_vector_fails_even_if_status_complete():
    event, old, new, risk = fixture_rows()
    old.embedding = None
    assert summarize(event, old, new, risk)["gates"]["vector_risk_complete"] is False


def test_pending_risk_is_not_finished_even_with_risk_row():
    event, old, new, risk = fixture_rows()
    event.risk_scoring_status = "pending"
    assert summarize(event, old, new, risk)["stages"]["risk_v2"] == "pending"


def test_report_does_not_include_sensitive_contents():
    event, old, new, risk = fixture_rows()
    old.raw_content = "a private document: top secret"
    old.raw_content_path = "/private/internal/file.txt"
    event.summary_text = "classified internal note"
    output = str(summarize(event, old, new, risk))
    assert "top secret" not in output
    assert "/private/internal/file.txt" not in output
    assert "classified internal" not in output


def test_timing_skips_negative_or_unavailable_intervals():
    t = datetime.now(timezone.utc)
    assert _elapsed_ms(t, t - timedelta(seconds=3)) is None
    assert _elapsed_ms(None, t) is None
    assert _elapsed_ms(t, t + timedelta(milliseconds=1)) == 1


def test_command_requires_explicit_tenant_and_event():
    with pytest.raises(SystemExit):
        parse_args([])
    org, event = uuid4(), uuid4()
    args = parse_args([
        "--organization-id", str(org),
        "--event-id", str(event),
        "--require-vector-risk",
    ])
    assert args.organization_id == org
    assert args.event_id == event
    assert args.require_vector_risk is True


def test_embedding_dimension_metadata_mismatch_is_incomplete():
    event, old, new, risk = fixture_rows()
    new.embedding_dimensions = 1536
    assert summarize(event, old, new, risk)["gates"]["vector_risk_complete"] is False


def test_risk_projection_mismatch_is_inconsistent():
    event, old, new, risk = fixture_rows()
    event.risk_score = 90.0
    assert summarize(event, old, new, risk)["stages"]["risk_v2"] == "inconsistent"


def test_cli_read_only_and_failure_exit(monkeypatch, capsys):
    import sys
    from types import ModuleType
    from scripts import audit_pipeline_effectiveness as cli
    from src.services import pipeline_effectiveness_evidence as service

    class FakeDB:
        def __init__(self):
            self.commands = []
            self.rolled_back = False
            self.closed = False

        def execute(self, query):
            self.commands.append(str(query))

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    db = FakeDB()
    fake_db_module = ModuleType("src.core.database")
    fake_db_module.SessionLocal = lambda: db
    monkeypatch.setitem(sys.modules, "src.core.database", fake_db_module)
    monkeypatch.setattr(service, "read_pipeline_event_evidence", lambda *a, **kw: {
        "gates": {"vector_risk_complete": False},
    })
    org, event = uuid4(), uuid4()
    assert cli.main(["--organization-id", str(org), "--event-id", str(event), "--require-vector-risk"]) == 2
    assert db.commands == ["SET TRANSACTION READ ONLY"]
    assert db.rolled_back is True and db.closed is True
    assert '"vector_risk_complete": false' in capsys.readouterr().out


def test_cli_not_found_is_scoped_error(monkeypatch, capsys):
    import sys
    from types import ModuleType
    from scripts import audit_pipeline_effectiveness as cli
    from src.services import pipeline_effectiveness_evidence as service

    class FakeDB:
        def execute(self, query):
            pass
        def rollback(self):
            pass
        def close(self):
            pass
    fake_db_module = ModuleType("src.core.database")
    fake_db_module.SessionLocal = FakeDB
    monkeypatch.setitem(sys.modules, "src.core.database", fake_db_module)
    def not_found(*args, **kwargs):
        raise LookupError("No drift event found for the specified organization.")
    monkeypatch.setattr(service, "read_pipeline_event_evidence", not_found)
    org, event = uuid4(), uuid4()
    assert cli.main(["--organization-id", str(org), "--event-id", str(event)]) == 1
    assert '"error": "event_not_found"' in capsys.readouterr().err


def test_partial_embedding_progress_is_pending_not_inconsistent():
    event, old, new, risk = fixture_rows()
    new.embedding_status = "pending"
    new.embedding = None
    result = summarize(event, old, new, risk)
    assert result["stages"]["embeddings"] == "pending"
    assert result["gates"]["vector_risk_complete"] is False
