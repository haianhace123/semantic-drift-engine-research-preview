from datetime import (
    datetime,
    timedelta,
    timezone,
)
import uuid

import pytest

import src.services.pattern_orchestrator as orchestrator
from src.services.pattern_detection_service import (
    PatternEventInput,
    RiskAssessmentInput,
    TopologyShiftInput,
)
from src.services.pattern_evidence_service import (
    PatternEvidenceBundle,
)

BASE = datetime(
    2026,
    8,
    31,
    2,
    0,
    tzinfo=timezone.utc,
)


def _id(
    value: int,
) -> uuid.UUID:
    return uuid.UUID(
        int=value
    )


def _bundle():
    organization_id = _id(
        1000
    )

    entity_id = _id(
        2000
    )

    events = tuple(
        PatternEventInput(
            event_id=_id(
                index
            ),
            entity_id=entity_id,
            occurred_at=(
                BASE
                + timedelta(
                    hours=index
                )
            ),
        )
        for index in (
            1,
            2,
            3,
        )
    )

    risks = tuple(
        RiskAssessmentInput(
            event_id=_id(
                index
            ),
            entity_id=entity_id,
            occurred_at=(
                BASE
                + timedelta(
                    hours=index
                )
            ),
            score=score,
        )
        for index, score in (
            (1, 0.20),
            (2, 0.40),
            (3, 0.65),
        )
    )

    topology = (
        TopologyShiftInput(
            event_id=_id(
                2
            ),
            entity_id=entity_id,
            observed_at=(
                BASE
                + timedelta(
                    hours=2
                )
            ),
            classification=(
                "centrality_gain"
            ),
            shift_score=0.30,
        ),
        TopologyShiftInput(
            event_id=_id(
                3
            ),
            entity_id=entity_id,
            observed_at=(
                BASE
                + timedelta(
                    hours=3
                )
            ),
            classification=(
                "downstream_gain"
            ),
            shift_score=0.55,
        ),
    )

    return PatternEvidenceBundle(
        organization_id=(
            organization_id
        ),
        as_of=(
            BASE
            + timedelta(
                days=1
            )
        ),
        window_start=(
            BASE
            - timedelta(
                days=29
            )
        ),
        lookback_days=30,
        events=events,
        risk_assessments=risks,
        topology_shifts=topology,
    )


def test_orchestrator_combines_all_pattern_types(
    monkeypatch,
):
    evidence = _bundle()

    monkeypatch.setattr(
        orchestrator,
        "load_pattern_evidence",
        lambda *args, **kwargs: evidence,
    )

    result = (
        orchestrator.detect_organization_patterns(
            object(),
            organization_id=(
                evidence.organization_id
            ),
            as_of=evidence.as_of,
        )
    )

    assert (
        result.organization_id
        == evidence.organization_id
    )

    assert (
        result.evidence_event_count
        == 3
    )

    assert (
        result.evidence_risk_count
        == 3
    )

    assert (
        result.evidence_topology_count
        == 2
    )

    assert {
        pattern.pattern_type
        for pattern in result.patterns
    } == {
        "repeated_drift",
        "risk_escalation",
        "topology_sequence",
    }


def test_orchestrator_returns_deterministic_order(
    monkeypatch,
):
    evidence = _bundle()

    monkeypatch.setattr(
        orchestrator,
        "load_pattern_evidence",
        lambda *args, **kwargs: evidence,
    )

    first = (
        orchestrator.detect_organization_patterns(
            object(),
            organization_id=(
                evidence.organization_id
            ),
            as_of=evidence.as_of,
        )
    )

    second = (
        orchestrator.detect_organization_patterns(
            object(),
            organization_id=(
                evidence.organization_id
            ),
            as_of=evidence.as_of,
        )
    )

    assert (
        first.patterns
        == second.patterns
    )


def test_orchestrator_passes_temporal_arguments(
    monkeypatch,
):
    evidence = _bundle()

    captured = {}

    def fake_loader(
        db,
        *,
        organization_id,
        as_of,
        knowledge_cutoff,
        knowledge_cutoff_record_id,
        lookback_days,
    ):
        captured[
            "db"
        ] = db

        captured[
            "organization_id"
        ] = organization_id

        captured[
            "as_of"
        ] = as_of

        captured[
            "knowledge_cutoff"
        ] = knowledge_cutoff

        captured[
            "knowledge_cutoff_record_id"
        ] = knowledge_cutoff_record_id

        captured[
            "lookback_days"
        ] = lookback_days

        return evidence

    monkeypatch.setattr(
        orchestrator,
        "load_pattern_evidence",
        fake_loader,
    )

    fake_db = object()

    knowledge_cutoff_record_id = uuid.UUID(
        "00000000-0000-0000-0000-000000000123"
    )

    orchestrator.detect_organization_patterns(
        fake_db,
        organization_id=(
            evidence.organization_id
        ),
        as_of=evidence.as_of,
        lookback_days=45,
        knowledge_cutoff=(
            evidence.as_of
            + timedelta(days=10)
        ),
        knowledge_cutoff_record_id=(
            knowledge_cutoff_record_id
        ),
    )

    assert (
        captured["db"]
        is fake_db
    )

    assert (
        captured[
            "organization_id"
        ]
        == evidence.organization_id
    )

    assert (
        captured["as_of"]
        == evidence.as_of
    )

    assert (
        captured[
            "lookback_days"
        ]
        == 45
    )

    assert captured[
        "knowledge_cutoff"
    ] == (
        evidence.as_of
        + timedelta(days=10)
    )

    assert (
        captured[
            "knowledge_cutoff_record_id"
        ]
        == knowledge_cutoff_record_id
    )

def test_orchestrator_forwards_detector_thresholds(
    monkeypatch,
):
    evidence = _bundle()

    monkeypatch.setattr(
        orchestrator,
        "load_pattern_evidence",
        lambda *args, **kwargs: evidence,
    )

    result = (
        orchestrator.detect_organization_patterns(
            object(),
            organization_id=(
                evidence.organization_id
            ),
            as_of=evidence.as_of,
            repeated_drift_minimum_events=4,
            risk_escalation_minimum_events=4,
            topology_sequence_minimum_events=3,
        )
    )

    assert (
        result.patterns
        == ()
    )


def test_orchestrator_forwards_risk_minimum_step(
    monkeypatch,
):
    evidence = _bundle()

    monkeypatch.setattr(
        orchestrator,
        "load_pattern_evidence",
        lambda *args, **kwargs: evidence,
    )

    result = (
        orchestrator.detect_organization_patterns(
            object(),
            organization_id=(
                evidence.organization_id
            ),
            as_of=evidence.as_of,
            repeated_drift_minimum_events=4,
            risk_escalation_minimum_step=0.30,
            topology_sequence_minimum_events=3,
        )
    )

    assert (
        result.patterns
        == ()
    )


def test_orchestrator_propagates_loader_validation(
    monkeypatch,
):
    def fake_loader(
        *args,
        **kwargs,
    ):
        raise ValueError(
            "invalid evidence request"
        )

    monkeypatch.setattr(
        orchestrator,
        "load_pattern_evidence",
        fake_loader,
    )

    with pytest.raises(
        ValueError,
        match="invalid evidence request",
    ):
        orchestrator.detect_organization_patterns(
            object(),
            organization_id=_id(
                1000
            ),
            as_of=BASE,
        )


def test_orchestrator_does_not_mutate_evidence(
    monkeypatch,
):
    evidence = _bundle()

    original_events = (
        evidence.events
    )

    original_risks = (
        evidence.risk_assessments
    )

    original_topology = (
        evidence.topology_shifts
    )

    monkeypatch.setattr(
        orchestrator,
        "load_pattern_evidence",
        lambda *args, **kwargs: evidence,
    )

    orchestrator.detect_organization_patterns(
        object(),
        organization_id=(
            evidence.organization_id
        ),
        as_of=evidence.as_of,
    )

    assert (
        evidence.events
        is original_events
    )

    assert (
        evidence.risk_assessments
        is original_risks
    )

    assert (
        evidence.topology_shifts
        is original_topology
    )