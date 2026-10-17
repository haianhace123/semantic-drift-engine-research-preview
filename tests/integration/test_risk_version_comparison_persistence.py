from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.database import SessionLocal
from src.services.risk_assessment_service import (
    persist_risk_assessment_v3,
)
from src.services.risk_version import (
    RISK_VERSION_V2,
    RISK_VERSION_V3,
)
from src.services.risk_version_comparison_service import (
    RiskVersionComparisonNotReadyError,
    compare_persisted_risk_versions,
)
from tests.integration.test_risk_assessment_persistence import (
    _cleanup,
    _persist_default_risk,
)
from tests.integration.test_risk_assessment_persistence_v3 import (
    _make_baseline_event,
)


def _persist_default_risk_v3(
    db,
    *,
    event,
):
    return persist_risk_assessment_v3(
        db,
        event=event,
        semantic_magnitude=0.8,
        legacy_impact=0.6,
        semantic_severity=0.7,
        page_context=0.5,
        confidence=0.9,
    )


def test_compare_persisted_v2_and_v3_assessments():
    db = SessionLocal()
    organization_id = None

    try:
        organization, event = _make_baseline_event(
            db,
            detected_at=datetime(
                2026,
                10,
                1,
                10,
                0,
                tzinfo=timezone.utc,
            ),
            suffix="risk-compare-pair",
        )
        organization_id = organization.id

        v2 = _persist_default_risk(
            db,
            event=event,
        )
        db.commit()

        v3 = _persist_default_risk_v3(
            db,
            event=event,
        )
        db.commit()

        result = compare_persisted_risk_versions(
            db,
            organization_id=organization.id,
            event_id=event.id,
        )

        assert result.baseline_version == RISK_VERSION_V2
        assert result.candidate_version == RISK_VERSION_V3

        assert result.baseline_assessment_id == v2.assessment_id
        assert result.candidate_assessment_id == v3.assessment_id

        assert result.baseline_score == v2.score
        assert result.candidate_score == v3.score
        assert result.score_delta == pytest.approx(
            v3.score - v2.score,
            abs=1e-6,
        )

        assert result.contributions.semantic_magnitude == 0.0
        assert result.contributions.legacy_impact == 0.0
        assert result.contributions.semantic_severity == 0.0
        assert result.contributions.page_context == 0.0
        assert result.contributions.confidence == 0.0

        assert result.contributions.topology == pytest.approx(
            v3.topology_contribution
            - v2.topology_contribution,
            abs=1e-6,
        )

        assert result.topology.score_delta == pytest.approx(
            v3.topology_score - v2.topology_score,
            abs=1e-6,
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_compare_requires_persisted_candidate_version():
    db = SessionLocal()
    organization_id = None

    try:
        organization, event = _make_baseline_event(
            db,
            detected_at=datetime(
                2026,
                10,
                1,
                11,
                0,
                tzinfo=timezone.utc,
            ),
            suffix="risk-compare-no-v3",
        )
        organization_id = organization.id

        _persist_default_risk(
            db,
            event=event,
        )
        db.commit()

        with pytest.raises(
            RiskVersionComparisonNotReadyError,
            match=RISK_VERSION_V3,
        ):
            compare_persisted_risk_versions(
                db,
                organization_id=organization.id,
                event_id=event.id,
            )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()
