from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from src.core.database import SessionLocal
from src.services.graph_metric_snapshot_service import (
    persist_graph_metric_snapshots_for_event,
)
from src.services.risk_assessment_service import (
    persist_risk_assessment_v2,
    persist_risk_assessment_v3,
)
from src.services.risk_shadow_evaluation_service import (
    evaluate_persisted_risk_shadow,
)
from src.services.risk_version_comparison_service import (
    compare_persisted_risk_versions,
)
from src.services.topology_shift_snapshot_service import (
    persist_topology_shift_snapshots_for_event,
)
from tests.integration.test_risk_scoring_v2 import (
    _cleanup,
    _make_entity,
    _make_event,
    _make_organization,
    _make_page,
    _make_relationship,
)


def _persist_risk_pair(
    db,
    *,
    event,
):
    inputs = {
        "semantic_magnitude": 0.8,
        "legacy_impact": 0.6,
        "semantic_severity": 0.7,
        "page_context": 0.5,
        "confidence": 0.9,
    }

    v2 = persist_risk_assessment_v2(
        db,
        event=event,
        **inputs,
    )
    db.commit()

    v3 = persist_risk_assessment_v3(
        db,
        event=event,
        **inputs,
    )
    db.commit()

    return v2, v3


def test_shadow_evaluation_observes_real_topology_divergence():
    db = SessionLocal()
    organization_id = None

    try:
        t1 = datetime(
            2036,
            8,
            30,
            8,
            0,
            tzinfo=timezone.utc,
        )
        t2 = datetime(
            2036,
            8,
            30,
            9,
            0,
            tzinfo=timezone.utc,
        )

        suffix = uuid.uuid4().hex[:8]

        organization = _make_organization(
            db,
            suffix=suffix,
        )
        organization_id = organization.id

        entity_a = _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-a",
        )
        entity_b = _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-b",
        )
        entity_c = _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-c",
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity_a.id,
            suffix=suffix,
        )

        event_t1 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t1,
            suffix="shadow-t1",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=entity_a.id,
            target_entity_id=entity_b.id,
            observed_at=t1,
            semantic_drift_event_id=event_t1.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=event_t1,
        )
        db.commit()

        persist_topology_shift_snapshots_for_event(
            db=db,
            event=event_t1,
        )
        db.commit()

        event_t2 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t2,
            suffix="shadow-t2",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=entity_b.id,
            target_entity_id=entity_c.id,
            observed_at=t2,
            semantic_drift_event_id=event_t2.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=event_t2,
        )
        db.commit()

        persist_topology_shift_snapshots_for_event(
            db=db,
            event=event_t2,
        )
        db.commit()

        v2, v3 = _persist_risk_pair(
            db,
            event=event_t2,
        )

        assert v2.has_previous_topology_state is True
        assert v3.has_previous_topology_state is True

        assert v2.topology_score > 0.0
        assert v3.topology_score > 0.0

        comparison = compare_persisted_risk_versions(
            db,
            organization_id=organization.id,
            event_id=event_t2.id,
        )

        assert comparison.topology.score_delta != pytest.approx(
            0.0,
            abs=1e-6,
        )
        assert comparison.contributions.topology != pytest.approx(
            0.0,
            abs=1e-6,
        )
        assert comparison.score_delta != pytest.approx(
            0.0,
            abs=1e-6,
        )

        assert comparison.contributions.semantic_magnitude == 0.0
        assert comparison.contributions.legacy_impact == 0.0
        assert comparison.contributions.semantic_severity == 0.0
        assert comparison.contributions.page_context == 0.0
        assert comparison.contributions.confidence == 0.0

        assert comparison.score_delta == pytest.approx(
            comparison.contributions.topology,
            abs=1e-6,
        )

        evaluation = evaluate_persisted_risk_shadow(
            db,
            organization_id=organization.id,
        )

        assert evaluation.coverage.total_events == 1
        assert evaluation.coverage.paired == 1
        assert evaluation.coverage.v2_only == 0
        assert evaluation.coverage.v3_only == 0
        assert evaluation.coverage.pair_coverage == 1.0

        assert evaluation.evaluation is not None
        assert evaluation.evaluation.paired_count == 1

        assert (
            evaluation.evaluation
            .non_topology_contribution_mismatches
            == 0
        )

        assert (
            evaluation.evaluation.score_delta.mean
            == pytest.approx(
                comparison.score_delta,
                abs=1e-6,
            )
        )
        assert (
            evaluation.evaluation
            .score_delta.mean_absolute
            > 0.0
        )
        assert (
            evaluation.evaluation
            .topology.mean_absolute_score_delta
            > 0.0
        )
        assert (
            evaluation.evaluation
            .topology.mean_absolute_contribution_delta
            > 0.0
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()

def test_persisted_shadow_evaluation_uses_one_select():
    from sqlalchemy import event as sqlalchemy_event

    from src.models.risk_assessment import RiskAssessment
    from src.services.risk_version import (
        RISK_VERSION_V2,
        RISK_VERSION_V3,
    )

    db = SessionLocal()
    organization_id = None

    try:
        suffix = uuid.uuid4().hex[:8]

        organization = _make_organization(
            db,
            suffix=suffix,
        )
        organization_id = organization.id

        entity = _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-entity",
        )
        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
            suffix=suffix,
        )

        events = []

        for index in range(3):
            drift_event = _make_event(
                db,
                organization_id=organization.id,
                monitored_page_id=page.id,
                detected_at=datetime(
                    2037,
                    1,
                    1,
                    10 + index,
                    0,
                    tzinfo=timezone.utc,
                ),
                suffix=f"query-count-{index}",
            )
            events.append(drift_event)

        db.commit()

        for drift_event in events:
            common = {
                "organization_id": organization.id,
                "assessed_at": datetime.now(timezone.utc),
                "semantic_drift_event_id": drift_event.id,
                "score": 0.4,
                "semantic_magnitude_contribution": 0.12,
                "legacy_impact_contribution": 0.06,
                "semantic_severity_contribution": 0.04,
                "page_context_contribution": 0.03,
                "confidence_contribution": 0.05,
                "topology_score": 0.4,
                "topology_contribution": 0.1,
                "topology_shift_component": 0.4,
                "topology_centrality_component": 0.4,
                "topology_downstream_component": 0.4,
                "has_topology_signal": True,
                "has_previous_topology_state": True,
            }

            db.add(
                RiskAssessment(
                    id=uuid.uuid4(),
                    risk_version=RISK_VERSION_V2,
                    **common,
                )
            )
            db.add(
                RiskAssessment(
                    id=uuid.uuid4(),
                    risk_version=RISK_VERSION_V3,
                    **common,
                )
            )

        db.commit()
        db.expire_all()

        select_count = 0

        def _count_selects(
            conn,
            cursor,
            statement,
            parameters,
            context,
            executemany,
        ):
            nonlocal select_count

            if statement.lstrip().upper().startswith("SELECT"):
                select_count += 1

        bind = db.get_bind()

        sqlalchemy_event.listen(
            bind,
            "before_cursor_execute",
            _count_selects,
        )

        try:
            result = evaluate_persisted_risk_shadow(
                db,
                organization_id=organization_id,
            )
        finally:
            sqlalchemy_event.remove(
                bind,
                "before_cursor_execute",
                _count_selects,
            )

        assert result.coverage.total_events == 3
        assert result.coverage.paired == 3
        assert result.coverage.v2_only == 0
        assert result.coverage.v3_only == 0
        assert result.evaluation is not None
        assert result.evaluation.paired_count == 3

        assert select_count == 1

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()
