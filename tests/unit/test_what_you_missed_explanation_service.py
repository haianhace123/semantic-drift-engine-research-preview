from datetime import (
    datetime,
    timedelta,
    timezone,
)
import uuid

import pytest

from src.services.what_you_missed_evidence_service import (
    MissedAnomalyEvidence,
    MissedEventEvidence,
    MissedPatternEvidence,
    MissedRiskEvidence,
    MissedTopologyEvidence,
    WhatYouMissedEvidenceBundle,
)
from src.services.what_you_missed_explanation_service import (
    _clamp01,
    _importance_level,
    _normalize_risk_score,
    explain_what_you_missed,
)


BASE = datetime(
    2026,
    9,
    2,
    3,
    0,
    tzinfo=timezone.utc,
)


def _event(
    *,
    event_id=None,
    occurred_at=BASE,
    name="OpenAI",
    magnitude=0.80,
    impact=80,
):
    return MissedEventEvidence(
        event_id=(
            event_id
            or uuid.uuid4()
        ),
        monitored_page_id=uuid.uuid4(),
        entity_id=uuid.uuid4(),
        entity_name=name,
        entity_domain="example.com",
        page_url="https://example.com/docs",
        page_type="docs",
        occurred_at=occurred_at,
        category="content",
        change_type="modified",
        summary_text="Terms changed.",
        semantic_magnitude=magnitude,
        impact_score=impact,
        confidence=0.90,
        risk_level="high",
        is_pattern_part=False,
        is_anomaly=False,
    )


def _risk(
    event_id,
    *,
    score=82.0,
):
    return MissedRiskEvidence(
        assessment_id=uuid.uuid4(),
        event_id=event_id,
        assessed_at=(
            BASE
            + timedelta(
                minutes=1
            )
        ),
        created_at=(
            BASE
            + timedelta(
                minutes=1
            )
        ),
        score=score,
        semantic_magnitude_contribution=20.0,
        legacy_impact_contribution=10.0,
        semantic_severity_contribution=8.0,
        page_context_contribution=8.0,
        confidence_contribution=7.0,
        topology_score=0.70,
        topology_contribution=18.0,
        topology_shift_component=0.60,
        topology_centrality_component=0.50,
        topology_downstream_component=0.70,
        has_topology_signal=True,
        has_previous_topology_state=True,
    )


def _topology(
    event_id,
    entity_id,
    *,
    classification="expanded",
    score=0.80,
):
    return MissedTopologyEvidence(
        topology_shift_id=uuid.uuid4(),
        event_id=event_id,
        entity_id=entity_id,
        observed_at=BASE,
        created_at=BASE,
        previous_observed_at=(
            BASE
            - timedelta(
                days=1
            )
        ),
        classification=classification,
        severity="high",
        shift_score=score,
        entity_count_delta=1,
        active_edge_count_delta=2,
        in_degree_delta=1,
        out_degree_delta=1,
        degree_delta=2,
        degree_centrality_delta=0.20,
        downstream_reach_delta=3,
        downstream_reach_ratio_delta=0.30,
    )


def _pattern(
    event_id,
    *,
    pattern_type="repeated_drift",
    significance=0.80,
):
    return MissedPatternEvidence(
        pattern_id=uuid.uuid4(),
        pattern_type=pattern_type,
        detection_method="deterministic_pattern_v1",
        event_ids=(
            event_id,
        ),
        matched_event_ids=(
            event_id,
        ),
        event_count=3,
        entity_count=1,
        time_span_days=3,
        significance_score=significance,
        first_event_at=BASE,
        last_event_at=BASE,
        detected_at=BASE,
        name="Pattern",
        description="Pattern description",
        summary="Pattern summary",
    )


def _anomaly(
    event_id,
    *,
    significance=0.90,
):
    return MissedAnomalyEvidence(
        anomaly_id=uuid.uuid4(),
        event_id=event_id,
        anomaly_type="activity_burst",
        detection_method=(
            "deterministic_activity_burst_v1"
        ),
        expected_value=1.5,
        actual_value=5.0,
        deviation_score=0.80,
        significance=significance,
        baseline_days=28,
        window_days=7,
        detected_at=BASE,
        description="Activity burst.",
    )


def _bundle(
    *,
    events=(),
    risks=(),
    topology=(),
    patterns=(),
    anomalies=(),
):
    return WhatYouMissedEvidenceBundle(
        organization_id=uuid.uuid4(),
        since=(
            BASE
            - timedelta(
                hours=1
            )
        ),
        until=(
            BASE
            + timedelta(
                hours=1
            )
        ),
        events=tuple(
            events
        ),
        risks=tuple(
            risks
        ),
        topology=tuple(
            topology
        ),
        patterns=tuple(
            patterns
        ),
        anomalies=tuple(
            anomalies
        ),
    )


@pytest.mark.parametrize(
    (
        "value",
        "expected",
    ),
    [
        (-1.0, 0.0),
        (0.0, 0.0),
        (0.5, 0.5),
        (1.0, 1.0),
        (2.0, 1.0),
    ],
)
def test_clamp01(
    value,
    expected,
):
    assert (
        _clamp01(
            value
        )
        == expected
    )


@pytest.mark.parametrize(
    (
        "score",
        "expected",
    ),
    [
        (None, 0.0),
        (0.0, 0.0),
        (0.82, 0.82),
        (1.0, 1.0),
        (50.0, 0.50),
        (82.0, 0.82),
        (100.0, 1.0),
        (120.0, 1.0),
    ],
)
def test_normalize_risk_score(
    score,
    expected,
):
    assert (
        _normalize_risk_score(
            score
        )
        == expected
    )


@pytest.mark.parametrize(
    (
        "score",
        "expected",
    ),
    [
        (0.10, "low"),
        (0.39, "low"),
        (0.40, "medium"),
        (0.64, "medium"),
        (0.65, "high"),
        (0.84, "high"),
        (0.85, "critical"),
        (1.0, "critical"),
    ],
)
def test_importance_level(
    score,
    expected,
):
    assert (
        _importance_level(
            score
        )
        == expected
    )


def test_empty_bundle_returns_empty_explanation():
    evidence = _bundle()

    result = (
        explain_what_you_missed(
            evidence
        )
    )

    assert result.event_count == 0
    assert result.explained_event_count == 0
    assert result.items == tuple()


def test_high_risk_event_uses_risk_as_primary_signal():
    event = _event()
    risk = _risk(
        event.event_id,
        score=0.82,
    )

    result = (
        explain_what_you_missed(
            _bundle(
                events=[
                    event,
                ],
                risks=[
                    risk,
                ],
            )
        )
    )

    item = result.items[0]

    assert item.event_id == event.event_id
    assert item.risk_score == 0.82

    assert (
        item.importance_score
        >= 0.82 * 0.85
    )

    assert "HIGH_RISK" in {
        reason.code
        for reason in item.reasons
    }

    assert (
        item.headline
        == "High-risk change detected for OpenAI"
    )


def test_pattern_is_attached_only_through_matched_event_ids():
    first = _event(
        name="First",
    )

    second = _event(
        name="Second",
    )

    pattern = _pattern(
        first.event_id
    )

    result = (
        explain_what_you_missed(
            _bundle(
                events=[
                    first,
                    second,
                ],
                patterns=[
                    pattern,
                ],
            )
        )
    )

    by_id = {
        item.event_id: item
        for item in result.items
    }

    assert (
        pattern.pattern_id
        in by_id[
            first.event_id
        ].pattern_ids
    )

    assert (
        pattern.pattern_id
        not in by_id[
            second.event_id
        ].pattern_ids
    )


def test_anomaly_generates_activity_burst_reason():
    event = _event()
    anomaly = _anomaly(
        event.event_id
    )

    result = (
        explain_what_you_missed(
            _bundle(
                events=[
                    event,
                ],
                anomalies=[
                    anomaly,
                ],
            )
        )
    )

    item = result.items[0]

    assert "ACTIVITY_BURST" in {
        reason.code
        for reason in item.reasons
    }

    assert (
        item.headline
        == "Unusual activity detected for OpenAI"
    )


@pytest.mark.parametrize(
    (
        "classification",
        "reason_code",
    ),
    [
        (
            "expanded",
            "TOPOLOGY_EXPANSION",
        ),
        (
            "contracted",
            "TOPOLOGY_CONTRACTION",
        ),
        (
            "centrality_gain",
            "TOPOLOGY_CENTRALITY_GAIN",
        ),
        (
            "centrality_loss",
            "TOPOLOGY_CENTRALITY_LOSS",
        ),
        (
            "downstream_gain",
            "DOWNSTREAM_REACH_GAIN",
        ),
        (
            "downstream_loss",
            "DOWNSTREAM_REACH_LOSS",
        ),
    ],
)
def test_topology_classification_generates_reason(
    classification,
    reason_code,
):
    event = _event()

    topology = _topology(
        event.event_id,
        event.entity_id,
        classification=classification,
    )

    result = (
        explain_what_you_missed(
            _bundle(
                events=[
                    event,
                ],
                topology=[
                    topology,
                ],
            )
        )
    )

    assert reason_code in {
        reason.code
        for reason in result.items[
            0
        ].reasons
    }


@pytest.mark.parametrize(
    (
        "pattern_type",
        "reason_code",
    ),
    [
        (
            "repeated_drift",
            "REPEATED_DRIFT",
        ),
        (
            "risk_escalation",
            "RISK_ESCALATION",
        ),
        (
            "topology_sequence",
            "TOPOLOGY_SEQUENCE",
        ),
    ],
)
def test_pattern_type_generates_reason(
    pattern_type,
    reason_code,
):
    event = _event()

    pattern = _pattern(
        event.event_id,
        pattern_type=pattern_type,
    )

    result = (
        explain_what_you_missed(
            _bundle(
                events=[
                    event,
                ],
                patterns=[
                    pattern,
                ],
            )
        )
    )

    assert reason_code in {
        reason.code
        for reason in result.items[
            0
        ].reasons
    }


def test_semantic_and_impact_signals_are_explainable():
    event = _event(
        magnitude=0.90,
        impact=90,
    )

    result = (
        explain_what_you_missed(
            _bundle(
                events=[
                    event,
                ]
            )
        )
    )

    codes = {
        reason.code
        for reason in result.items[
            0
        ].reasons
    }

    assert (
        "HIGH_SEMANTIC_MAGNITUDE"
        in codes
    )

    assert "HIGH_IMPACT" in codes


def test_supplemental_signals_are_bounded():
    event = _event(
        magnitude=1.0,
        impact=100,
    )

    risk = _risk(
        event.event_id,
        score=100.0,
    )

    topology = _topology(
        event.event_id,
        event.entity_id,
        score=1.0,
    )

    pattern = _pattern(
        event.event_id,
        significance=1.0,
    )

    anomaly = _anomaly(
        event.event_id,
        significance=1.0,
    )

    result = (
        explain_what_you_missed(
            _bundle(
                events=[
                    event,
                ],
                risks=[
                    risk,
                ],
                topology=[
                    topology,
                ],
                patterns=[
                    pattern,
                ],
                anomalies=[
                    anomaly,
                ],
            )
        )
    )

    item = result.items[0]

    assert item.importance_score == 1.0
    assert item.importance_level == "critical"


def test_duplicate_reason_codes_are_deduplicated():
    event = _event()

    first = _topology(
        event.event_id,
        event.entity_id,
        classification="expanded",
        score=0.40,
    )

    second = _topology(
        event.event_id,
        event.entity_id,
        classification="expanded",
        score=0.90,
    )

    result = (
        explain_what_you_missed(
            _bundle(
                events=[
                    event,
                ],
                topology=[
                    first,
                    second,
                ],
            )
        )
    )

    reasons = [
        reason
        for reason in result.items[
            0
        ].reasons
        if (
            reason.code
            == "TOPOLOGY_EXPANSION"
        )
    ]

    assert len(
        reasons
    ) == 1

    assert (
        reasons[0].weight
        == pytest.approx(
            0.09
        )
    )


def test_ranking_prefers_higher_importance():
    low = _event(
        occurred_at=(
            BASE
            + timedelta(
                minutes=10
            )
        ),
        name="Low",
        magnitude=0.20,
        impact=20,
    )

    high = _event(
        occurred_at=BASE,
        name="High",
        magnitude=0.90,
        impact=90,
    )

    result = (
        explain_what_you_missed(
            _bundle(
                events=[
                    low,
                    high,
                ]
            )
        )
    )

    assert (
        result.items[
            0
        ].event_id
        == high.event_id
    )


def test_equal_importance_prefers_newer_event():
    older = _event(
        occurred_at=BASE,
        name="Older",
        magnitude=0.50,
        impact=50,
    )

    newer = _event(
        occurred_at=(
            BASE
            + timedelta(
                minutes=10
            )
        ),
        name="Newer",
        magnitude=0.50,
        impact=50,
    )

    result = (
        explain_what_you_missed(
            _bundle(
                events=[
                    older,
                    newer,
                ]
            )
        )
    )

    assert (
        result.items[
            0
        ].event_id
        == newer.event_id
    )


def test_limit_does_not_change_total_event_count():
    events = [
        _event(
            occurred_at=(
                BASE
                + timedelta(
                    minutes=index
                )
            )
        )
        for index in range(
            5
        )
    ]

    result = (
        explain_what_you_missed(
            _bundle(
                events=events
            ),
            limit=2,
        )
    )

    assert result.event_count == 5
    assert result.explained_event_count == 2
    assert len(result.items) == 2


@pytest.mark.parametrize(
    "limit",
    [
        0,
        -1,
        True,
        1.5,
        "2",
    ],
)
def test_invalid_limit_is_rejected(
    limit,
):
    with pytest.raises(
        ValueError
    ):
        explain_what_you_missed(
            _bundle(),
            limit=limit,
        )


def test_invalid_evidence_type_is_rejected():
    with pytest.raises(
        TypeError
    ):
        explain_what_you_missed(
            object()
        )


def test_input_evidence_is_not_mutated():
    event = _event()
    risk = _risk(
        event.event_id
    )

    evidence = _bundle(
        events=[
            event,
        ],
        risks=[
            risk,
        ],
    )

    original_events = (
        evidence.events
    )

    original_risks = (
        evidence.risks
    )

    explain_what_you_missed(
        evidence
    )

    assert (
        evidence.events
        is original_events
    )

    assert (
        evidence.risks
        is original_risks
    )