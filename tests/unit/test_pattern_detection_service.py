from datetime import (
    datetime,
    timedelta,
    timezone,
)
import uuid

import pytest

from src.services.pattern_detection_service import (
    PatternEventInput,
    RiskAssessmentInput,
    TopologyShiftInput,
    detect_repeated_drift_patterns,
    detect_risk_escalation_patterns,
    detect_topology_sequence_patterns,
)


BASE = datetime(
    2026,
    8,
    31,
    tzinfo=timezone.utc,
)


def _id(
    value: int,
) -> uuid.UUID:
    return uuid.UUID(
        int=value
    )


def test_repeated_drift_requires_minimum_events():
    entity_id = _id(
        100
    )

    events = [
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
        )
    ]

    assert (
        detect_repeated_drift_patterns(
            events
        )
        == ()
    )


def test_repeated_drift_is_deterministic():
    entity_id = _id(
        100
    )

    events = [
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
            3,
            1,
            2,
        )
    ]

    result = (
        detect_repeated_drift_patterns(
            events
        )
    )

    assert len(result) == 1

    pattern = result[0]

    assert (
        pattern.pattern_type
        == "repeated_drift"
    )

    assert pattern.event_ids == (
        _id(1),
        _id(2),
        _id(3),
    )

    assert pattern.event_count == 3

    assert (
        0.0
        <= pattern.significance_score
        <= 1.0
    )


def test_repeated_drift_isolated_by_entity():
    first_entity = _id(
        100
    )

    second_entity = _id(
        200
    )

    events = []

    for index in range(
        1,
        4,
    ):
        events.append(
            PatternEventInput(
                event_id=_id(
                    index
                ),
                entity_id=first_entity,
                occurred_at=(
                    BASE
                    + timedelta(
                        hours=index
                    )
                ),
            )
        )

    for index in range(
        10,
        13,
    ):
        events.append(
            PatternEventInput(
                event_id=_id(
                    index
                ),
                entity_id=second_entity,
                occurred_at=(
                    BASE
                    + timedelta(
                        hours=index
                    )
                ),
            )
        )

    result = (
        detect_repeated_drift_patterns(
            events
        )
    )

    assert len(result) == 2

    assert {
        pattern.entity_id
        for pattern in result
    } == {
        first_entity,
        second_entity,
    }


def test_risk_escalation_detects_three_steps():
    entity_id = _id(
        100
    )

    assessments = [
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
            (2, 0.35),
            (3, 0.55),
        )
    ]

    result = (
        detect_risk_escalation_patterns(
            assessments
        )
    )

    assert len(result) == 1

    assert (
        result[0].pattern_type
        == "risk_escalation"
    )

    assert result[0].event_ids == (
        _id(1),
        _id(2),
        _id(3),
    )


def test_risk_escalation_breaks_on_non_increase():
    entity_id = _id(
        100
    )

    assessments = [
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
            (3, 0.35),
            (4, 0.50),
        )
    ]

    assert (
        detect_risk_escalation_patterns(
            assessments
        )
        == ()
    )


def test_risk_escalation_respects_minimum_step():
    entity_id = _id(
        100
    )

    assessments = [
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
            (2, 0.23),
            (3, 0.40),
        )
    ]

    assert (
        detect_risk_escalation_patterns(
            assessments,
            minimum_step=0.05,
        )
        == ()
    )


def test_topology_sequence_detects_gain_family():
    entity_id = _id(
        100
    )

    shifts = [
        TopologyShiftInput(
            event_id=_id(
                1
            ),
            entity_id=entity_id,
            observed_at=BASE,
            classification=(
                "centrality_gain"
            ),
            shift_score=0.30,
        ),
        TopologyShiftInput(
            event_id=_id(
                2
            ),
            entity_id=entity_id,
            observed_at=(
                BASE
                + timedelta(
                    hours=1
                )
            ),
            classification=(
                "downstream_gain"
            ),
            shift_score=0.60,
        ),
    ]

    result = (
        detect_topology_sequence_patterns(
            shifts
        )
    )

    assert len(result) == 1

    assert (
        result[0].pattern_type
        == "topology_sequence"
    )

    assert result[0].event_ids == (
        _id(1),
        _id(2),
    )


def test_topology_sequence_direction_change_breaks_sequence():
    entity_id = _id(
        100
    )

    shifts = [
        TopologyShiftInput(
            event_id=_id(
                1
            ),
            entity_id=entity_id,
            observed_at=BASE,
            classification="expanded",
            shift_score=0.4,
        ),
        TopologyShiftInput(
            event_id=_id(
                2
            ),
            entity_id=entity_id,
            observed_at=(
                BASE
                + timedelta(
                    hours=1
                )
            ),
            classification="contracted",
            shift_score=0.4,
        ),
    ]

    assert (
        detect_topology_sequence_patterns(
            shifts
        )
        == ()
    )


def test_topology_stable_breaks_sequence():
    entity_id = _id(
        100
    )

    shifts = [
        TopologyShiftInput(
            event_id=_id(
                1
            ),
            entity_id=entity_id,
            observed_at=BASE,
            classification="expanded",
            shift_score=0.4,
        ),
        TopologyShiftInput(
            event_id=_id(
                2
            ),
            entity_id=entity_id,
            observed_at=(
                BASE
                + timedelta(
                    hours=1
                )
            ),
            classification="stable",
            shift_score=0.0,
        ),
        TopologyShiftInput(
            event_id=_id(
                3
            ),
            entity_id=entity_id,
            observed_at=(
                BASE
                + timedelta(
                    hours=2
                )
            ),
            classification="expanded",
            shift_score=0.5,
        ),
    ]

    assert (
        detect_topology_sequence_patterns(
            shifts
        )
        == ()
    )


@pytest.mark.parametrize(
    (
        "function",
        "keyword",
        "value",
    ),
    [
        (
            detect_repeated_drift_patterns,
            "minimum_events",
            1,
        ),
        (
            detect_risk_escalation_patterns,
            "minimum_events",
            1,
        ),
        (
            detect_topology_sequence_patterns,
            "minimum_events",
            1,
        ),
    ],
)
def test_minimum_event_validation(
    function,
    keyword,
    value,
):
    with pytest.raises(
        ValueError
    ):
        function(
            [],
            **{
                keyword:
                    value
            },
        )


def test_risk_minimum_step_validation():
    with pytest.raises(
        ValueError
    ):
        detect_risk_escalation_patterns(
            [],
            minimum_step=-0.01,
        )