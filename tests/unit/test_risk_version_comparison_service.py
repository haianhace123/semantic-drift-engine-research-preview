import uuid

import pytest

from src.models.risk_assessment import RiskAssessment
from src.services.risk_version import (
    RISK_VERSION_V2,
    RISK_VERSION_V3,
)
from src.services.risk_version_comparison_service import (
    RiskVersionComparisonNotReadyError,
    compare_persisted_risk_versions,
)


class QueryStub:
    def __init__(self, rows):
        self._rows = rows
        self._version = None

    def filter(self, *criteria):
        for criterion in criteria:
            right = getattr(criterion, "right", None)
            value = getattr(right, "value", None)

            if value in (RISK_VERSION_V2, RISK_VERSION_V3):
                self._version = value

        return self

    def one_or_none(self):
        return self._rows.get(self._version)


class SessionStub:
    def __init__(self, rows):
        self._rows = rows

    def query(self, model):
        assert model is RiskAssessment
        return QueryStub(self._rows)


def _assessment(
    *,
    organization_id,
    event_id,
    version,
    score,
    topology_score,
    topology_contribution,
    shift,
    centrality,
    downstream,
):
    row = RiskAssessment()
    row.id = uuid.uuid4()
    row.organization_id = organization_id
    row.semantic_drift_event_id = event_id
    row.risk_version = version
    row.score = score

    row.semantic_magnitude_contribution = 0.12
    row.legacy_impact_contribution = 0.06
    row.semantic_severity_contribution = 0.04
    row.page_context_contribution = 0.03
    row.confidence_contribution = 0.05

    row.topology_score = topology_score
    row.topology_contribution = topology_contribution
    row.topology_shift_component = shift
    row.topology_centrality_component = centrality
    row.topology_downstream_component = downstream

    row.has_topology_signal = True
    row.has_previous_topology_state = True

    return row


def test_compare_persisted_risk_versions_reports_factual_deltas():
    organization_id = uuid.uuid4()
    event_id = uuid.uuid4()

    v2 = _assessment(
        organization_id=organization_id,
        event_id=event_id,
        version=RISK_VERSION_V2,
        score=0.45,
        topology_score=0.60,
        topology_contribution=0.15,
        shift=0.40,
        centrality=0.80,
        downstream=0.80,
    )
    v3 = _assessment(
        organization_id=organization_id,
        event_id=event_id,
        version=RISK_VERSION_V3,
        score=0.40,
        topology_score=0.40,
        topology_contribution=0.10,
        shift=0.40,
        centrality=0.30,
        downstream=0.20,
    )

    result = compare_persisted_risk_versions(
        SessionStub(
            {
                RISK_VERSION_V2: v2,
                RISK_VERSION_V3: v3,
            }
        ),
        organization_id=organization_id,
        event_id=event_id,
    )

    assert result.organization_id == organization_id
    assert result.event_id == event_id
    assert result.baseline_version == RISK_VERSION_V2
    assert result.candidate_version == RISK_VERSION_V3
    assert result.baseline_assessment_id == v2.id
    assert result.candidate_assessment_id == v3.id

    assert result.baseline_score == 0.45
    assert result.candidate_score == 0.40
    assert result.score_delta == -0.05
    assert result.direction == "decreased"

    assert result.contributions.semantic_magnitude == 0.0
    assert result.contributions.legacy_impact == 0.0
    assert result.contributions.semantic_severity == 0.0
    assert result.contributions.page_context == 0.0
    assert result.contributions.confidence == 0.0
    assert result.contributions.topology == -0.05

    assert result.topology.score_delta == -0.20
    assert result.topology.shift_component_delta == 0.0
    assert result.topology.centrality_component_delta == -0.50
    assert result.topology.downstream_component_delta == -0.60
    assert result.topology.baseline_shift_component == 0.40
    assert result.topology.candidate_shift_component == 0.40
    assert result.topology.baseline_centrality_component == 0.80
    assert result.topology.candidate_centrality_component == 0.30
    assert result.topology.baseline_downstream_component == 0.80
    assert result.topology.candidate_downstream_component == 0.20
    assert result.topology.signal_state_changed is False
    assert result.topology.previous_state_changed is False
    assert result.topology.baseline_has_signal is True
    assert result.topology.candidate_has_signal is True
    assert result.topology.baseline_has_previous_state is True
    assert result.topology.candidate_has_previous_state is True


def test_compare_persisted_risk_versions_reports_increase():
    organization_id = uuid.uuid4()
    event_id = uuid.uuid4()

    v2 = _assessment(
        organization_id=organization_id,
        event_id=event_id,
        version=RISK_VERSION_V2,
        score=0.30,
        topology_score=0.20,
        topology_contribution=0.05,
        shift=0.50,
        centrality=0.10,
        downstream=0.10,
    )
    v3 = _assessment(
        organization_id=organization_id,
        event_id=event_id,
        version=RISK_VERSION_V3,
        score=0.375,
        topology_score=0.50,
        topology_contribution=0.125,
        shift=0.50,
        centrality=0.10,
        downstream=0.10,
    )

    result = compare_persisted_risk_versions(
        SessionStub(
            {
                RISK_VERSION_V2: v2,
                RISK_VERSION_V3: v3,
            }
        ),
        organization_id=organization_id,
        event_id=event_id,
    )

    assert result.score_delta == 0.075
    assert result.direction == "increased"
    assert result.topology.score_delta == 0.30
    assert result.contributions.topology == 0.075


def test_compare_persisted_risk_versions_reports_unchanged():
    organization_id = uuid.uuid4()
    event_id = uuid.uuid4()

    assessment = _assessment(
        organization_id=organization_id,
        event_id=event_id,
        version=RISK_VERSION_V2,
        score=0.40,
        topology_score=0.40,
        topology_contribution=0.10,
        shift=0.40,
        centrality=0.40,
        downstream=0.40,
    )
    candidate = _assessment(
        organization_id=organization_id,
        event_id=event_id,
        version=RISK_VERSION_V3,
        score=0.40,
        topology_score=0.40,
        topology_contribution=0.10,
        shift=0.40,
        centrality=0.40,
        downstream=0.40,
    )

    result = compare_persisted_risk_versions(
        SessionStub(
            {
                RISK_VERSION_V2: assessment,
                RISK_VERSION_V3: candidate,
            }
        ),
        organization_id=organization_id,
        event_id=event_id,
    )

    assert result.score_delta == 0.0
    assert result.direction == "unchanged"
    assert result.topology.score_delta == 0.0
    assert result.contributions.topology == 0.0


def test_compare_requires_both_persisted_versions():
    organization_id = uuid.uuid4()
    event_id = uuid.uuid4()

    with pytest.raises(
        RiskVersionComparisonNotReadyError,
        match="deterministic_v3",
    ):
        compare_persisted_risk_versions(
            SessionStub(
                {
                    RISK_VERSION_V2: _assessment(
                        organization_id=organization_id,
                        event_id=event_id,
                        version=RISK_VERSION_V2,
                        score=0.40,
                        topology_score=0.40,
                        topology_contribution=0.10,
                        shift=0.40,
                        centrality=0.40,
                        downstream=0.40,
                    )
                }
            ),
            organization_id=organization_id,
            event_id=event_id,
        )
