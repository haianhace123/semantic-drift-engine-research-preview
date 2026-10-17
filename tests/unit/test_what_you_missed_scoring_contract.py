from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from src.services.what_you_missed_evidence_service import (
    MissedEventEvidence,
    MissedRiskEvidence,
    MissedTopologyEvidence,
    WhatYouMissedEvidenceBundle,
)
from src.services.what_you_missed_explanation_service import (
    explain_what_you_missed,
)


BASE = datetime(
    2026,
    9,
    2,
    12,
    0,
    tzinfo=timezone.utc,
)


def _event(
    *,
    semantic_magnitude: float = 0.90,
    impact_score: int = 90,
) -> MissedEventEvidence:
    return MissedEventEvidence(
        event_id=uuid.uuid4(),
        monitored_page_id=uuid.uuid4(),
        entity_id=uuid.uuid4(),
        entity_name="Example",
        entity_domain="example.com",
        page_url="https://example.com/pricing",
        page_type="pricing",
        occurred_at=BASE,
        category="pricing",
        change_type="modified",
        summary_text="Pricing materially changed.",
        semantic_magnitude=semantic_magnitude,
        impact_score=impact_score,
        confidence=0.95,
        risk_level="high",
        is_pattern_part=False,
        is_anomaly=False,
    )


def _risk(
    *,
    event_id: uuid.UUID,
    score: float,
) -> MissedRiskEvidence:
    return MissedRiskEvidence(
        assessment_id=uuid.uuid4(),
        event_id=event_id,
        assessed_at=BASE,
        created_at=BASE,
        score=score,
        semantic_magnitude_contribution=0.25,
        legacy_impact_contribution=0.15,
        semantic_severity_contribution=0.10,
        page_context_contribution=0.10,
        confidence_contribution=0.10,
        topology_score=0.80,
        topology_contribution=0.20,
        topology_shift_component=0.80,
        topology_centrality_component=0.70,
        topology_downstream_component=0.60,
        has_topology_signal=True,
        has_previous_topology_state=True,
    )


def _topology(
    *,
    event_id: uuid.UUID,
    entity_id: uuid.UUID,
    shift_score: float = 1.0,
) -> MissedTopologyEvidence:
    return MissedTopologyEvidence(
        topology_shift_id=uuid.uuid4(),
        event_id=event_id,
        entity_id=entity_id,
        observed_at=BASE,
        created_at=BASE,
        previous_observed_at=BASE,
        classification="centrality_gain",
        severity="high",
        shift_score=shift_score,
        entity_count_delta=0,
        active_edge_count_delta=2,
        in_degree_delta=1,
        out_degree_delta=1,
        degree_delta=2,
        degree_centrality_delta=0.50,
        downstream_reach_delta=3,
        downstream_reach_ratio_delta=0.40,
    )


def _bundle(
    *,
    event: MissedEventEvidence,
    risks: tuple[MissedRiskEvidence, ...] = (),
    topology: tuple[MissedTopologyEvidence, ...] = (),
) -> WhatYouMissedEvidenceBundle:
    return WhatYouMissedEvidenceBundle(
        organization_id=uuid.uuid4(),
        since=BASE,
        until=BASE.replace(hour=13),
        events=(event,),
        risks=risks,
        topology=topology,
        patterns=(),
        anomalies=(),
    )


def test_risk_v2_is_not_double_counted_with_semantic_impact_or_topology():
    event = _event(
        semantic_magnitude=1.0,
        impact_score=100,
    )

    risk = _risk(
        event_id=event.event_id,
        score=0.70,
    )

    topology = _topology(
        event_id=event.event_id,
        entity_id=event.entity_id,
        shift_score=1.0,
    )

    result = explain_what_you_missed(
        _bundle(
            event=event,
            risks=(risk,),
            topology=(topology,),
        )
    )

    assert result.explained_event_count == 1

    item = result.items[0]

    #
    # Risk v2 is the authoritative base score.
    #
    # Semantic magnitude, impact and topology are still explanation
    # reasons, but must not push the importance score above the
    # persisted Risk v2 score by themselves.
    #
    assert item.risk_score == pytest.approx(0.70)
    assert item.importance_score == pytest.approx(0.70)

    reason_codes = {
        reason.code
        for reason in item.reasons
    }

    assert "HIGH_RISK" in reason_codes
    assert "HIGH_SEMANTIC_MAGNITUDE" in reason_codes
    assert "HIGH_IMPACT" in reason_codes
    assert "TOPOLOGY_CENTRALITY_GAIN" in reason_codes


def test_persisted_risk_score_uses_zero_to_one_contract():
    event = _event()

    risk = _risk(
        event_id=event.event_id,
        score=0.92,
    )

    result = explain_what_you_missed(
        _bundle(
            event=event,
            risks=(risk,),
        )
    )

    item = result.items[0]

    assert item.risk_score == pytest.approx(0.92)
    assert item.importance_score == pytest.approx(0.92)


def test_no_risk_uses_semantic_impact_and_topology_fallback():
    event = _event(
        semantic_magnitude=0.80,
        impact_score=60,
    )

    topology = _topology(
        event_id=event.event_id,
        entity_id=event.entity_id,
        shift_score=0.40,
    )

    result = explain_what_you_missed(
        _bundle(
            event=event,
            topology=(topology,),
        )
    )

    item = result.items[0]

    expected = (
        0.55 * 0.80
        + 0.30 * 0.60
        + 0.15 * 0.40
    )

    assert item.risk_score is None
    assert item.importance_score == pytest.approx(
        round(expected, 6)
    )


def test_topology_reason_is_preserved_even_when_risk_already_contains_it():
    event = _event()

    risk = _risk(
        event_id=event.event_id,
        score=0.75,
    )

    topology = _topology(
        event_id=event.event_id,
        entity_id=event.entity_id,
        shift_score=0.90,
    )

    result = explain_what_you_missed(
        _bundle(
            event=event,
            risks=(risk,),
            topology=(topology,),
        )
    )

    item = result.items[0]

    assert item.importance_score == pytest.approx(0.75)

    reason_codes = {
        reason.code
        for reason in item.reasons
    }

    assert "TOPOLOGY_CENTRALITY_GAIN" in reason_codes