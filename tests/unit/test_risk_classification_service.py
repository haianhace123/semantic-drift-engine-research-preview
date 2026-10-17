import pytest

from src.services.risk_classification_service import (
    RISK_CRITICAL_THRESHOLD,
    RISK_HIGH_THRESHOLD,
    RISK_MEDIUM_THRESHOLD,
    classify_risk_score,
    clamp_unit,
    normalize_risk_score,
)


@pytest.mark.parametrize(
    (
        "value",
        "expected",
    ),
    (
        (-1.0, 0.0),
        (0.0, 0.0),
        (0.25, 0.25),
        (1.0, 1.0),
        (2.0, 1.0),
    ),
)
def test_clamp_unit(
    value: float,
    expected: float,
) -> None:
    assert clamp_unit(
        value
    ) == expected


@pytest.mark.parametrize(
    (
        "score",
        "expected",
    ),
    (
        (None, 0.0),
        (-10.0, 0.0),
        (0.0, 0.0),
        (0.4, 0.4),
        (0.65, 0.65),
        (0.85, 0.85),
        (1.0, 1.0),
        (40.0, 0.4),
        (65.0, 0.65),
        (85.0, 0.85),
        (100.0, 1.0),
        (150.0, 1.0),
    ),
)
def test_normalize_risk_score(
    score: float | None,
    expected: float,
) -> None:
    assert normalize_risk_score(
        score
    ) == pytest.approx(
        expected
    )


@pytest.mark.parametrize(
    (
        "score",
        "expected",
    ),
    (
        (None, "low"),
        (0.0, "low"),
        (RISK_MEDIUM_THRESHOLD - 0.000001, "low"),
        (RISK_MEDIUM_THRESHOLD, "medium"),
        (RISK_HIGH_THRESHOLD - 0.000001, "medium"),
        (RISK_HIGH_THRESHOLD, "high"),
        (RISK_CRITICAL_THRESHOLD - 0.000001, "high"),
        (RISK_CRITICAL_THRESHOLD, "critical"),
        (1.0, "critical"),
    ),
)
def test_classify_normalized_risk_score(
    score: float | None,
    expected: str,
) -> None:
    assert classify_risk_score(
        score
    ) == expected


@pytest.mark.parametrize(
    (
        "score",
        "expected",
    ),
    (
        (39.9999, "low"),
        (40.0, "medium"),
        (64.9999, "medium"),
        (65.0, "high"),
        (84.9999, "high"),
        (85.0, "critical"),
        (100.0, "critical"),
    ),
)
def test_classify_percentage_risk_score(
    score: float,
    expected: str,
) -> None:
    assert classify_risk_score(
        score
    ) == expected