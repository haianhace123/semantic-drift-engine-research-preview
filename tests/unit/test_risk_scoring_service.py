import pytest

from src.services.risk_scoring_service import (
    RiskScoringValidationError,
    calculate_risk_score,
)


def test_calculate_risk_score_high_risk():
    result = calculate_risk_score(
        semantic_magnitude=0.8,
        impact_score=80,
        risk_level="high",
        confidence=0.9,
        context_factor=1.0,
    )

    assert result["risk_score"] == 86.0
    assert result["version"] == "v1"

    components = result["components"]

    assert (
        components["semantic_magnitude"]["points"]
        == 32.0
    )

    assert (
        components["impact"]["points"]
        == 20.0
    )

    assert (
        components["severity"]["points"]
        == 15.0
    )

    assert (
        components["context"]["points"]
        == 10.0
    )

    assert (
        components["confidence"]["points"]
        == 9.0
    )


def test_calculate_risk_score_is_deterministic():
    kwargs = {
        "semantic_magnitude": 0.42,
        "impact_score": 55,
        "risk_level": "medium",
        "confidence": 0.73,
        "context_factor": 0.85,
    }

    first = calculate_risk_score(
        **kwargs
    )

    second = calculate_risk_score(
        **kwargs
    )

    assert first == second


def test_percentage_confidence_is_normalized():
    result = calculate_risk_score(
        semantic_magnitude=0.0,
        impact_score=0,
        risk_level="unknown",
        confidence=75,
        context_factor=0.0,
    )

    assert result["risk_score"] == 7.5

    assert (
        result["components"]["confidence"]["value"]
        == 0.75
    )


@pytest.mark.parametrize(
    (
        "semantic_magnitude",
        "impact_score",
        "confidence",
        "context_factor",
    ),
    [
        (-0.01, 50, 0.5, 0.5),
        (1.01, 50, 0.5, 0.5),
        (0.5, -1, 0.5, 0.5),
        (0.5, 101, 0.5, 0.5),
        (0.5, 50, -0.1, 0.5),
        (0.5, 50, 101, 0.5),
        (0.5, 50, 0.5, -0.1),
        (0.5, 50, 0.5, 1.1),
    ],
)
def test_invalid_inputs_are_rejected(
    semantic_magnitude,
    impact_score,
    confidence,
    context_factor,
):
    with pytest.raises(
        RiskScoringValidationError
    ):
        calculate_risk_score(
            semantic_magnitude=semantic_magnitude,
            impact_score=impact_score,
            risk_level="medium",
            confidence=confidence,
            context_factor=context_factor,
        )