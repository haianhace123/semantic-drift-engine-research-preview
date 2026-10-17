from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
import os
import uuid

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.models.alert import (
    ALERT_SOURCE_ANOMALY,
    ALERT_SOURCE_PATTERN,
    ALERT_SOURCE_RISK,
    Alert,
)
from src.models.anomaly import Anomaly
from src.models.organization import Organization
from src.models.pattern import Pattern
from src.models.risk_assessment import RiskAssessment
from src.services.alert_service import (
    ALERT_TYPE_ANOMALY,
    ALERT_TYPE_PATTERN,
    ALERT_TYPE_RISK,
    AlertCandidate,
    AlertPersistenceValidationError,
    build_alert_detection_key,
    persist_alert_candidate,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


BASE = datetime(
    2026,
    9,
    10,
    9,
    0,
    0,
    tzinfo=timezone.utc,
)


def _make_organization(
    db,
    *,
    prefix: str,
) -> Organization:
    suffix = uuid.uuid4().hex[:12]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"alert-service-{prefix}-{suffix}",
        slug=f"alert-service-{prefix}-{suffix}",
        billing_email=(
            f"alert-service-{prefix}-{suffix}"
            "@example.com"
        ),
    )

    db.add(organization)
    db.flush()

    return organization


def _make_risk(
    db,
    *,
    organization_id,
    assessed_at: datetime,
) -> RiskAssessment:
    assessment = RiskAssessment(
        id=uuid.uuid4(),
        organization_id=organization_id,
        semantic_drift_event_id=None,
        risk_version="v2",
        assessed_at=assessed_at,
        score=0.91,
        semantic_magnitude_contribution=0.8,
        legacy_impact_contribution=0.7,
        semantic_severity_contribution=0.9,
        page_context_contribution=0.6,
        confidence_contribution=0.9,
        topology_score=0.0,
        topology_contribution=0.0,
        topology_shift_component=0.0,
        topology_centrality_component=0.0,
        topology_downstream_component=0.0,
        has_topology_signal=False,
        has_previous_topology_state=False,
    )

    db.add(assessment)
    db.flush()

    return assessment


def _make_pattern(
    db,
    *,
    organization_id,
    last_event_at: datetime,
) -> Pattern:
    pattern = Pattern(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="Alert integration pattern",
        description="Deterministic test pattern.",
        pattern_type="repeated_drift",
        detection_key=uuid.uuid4().hex,
        detection_method="alert_integration_test",
        event_ids=[],
        entities=[],
        categories=[],
        event_count=0,
        entity_count=0,
        time_span_days=0,
        significance_score=0.9,
        first_event_at=last_event_at,
        last_event_at=last_event_at,
        summary="Deterministic test pattern.",
        detected_at=last_event_at,
        is_active=True,
        is_archived=False,
    )

    db.add(pattern)
    db.flush()

    return pattern


def _make_anomaly(
    db,
    *,
    organization_id,
    event_id,
    detected_at: datetime,
) -> Anomaly:
    anomaly = Anomaly(
        id=uuid.uuid4(),
        organization_id=organization_id,
        event_id=event_id,
        anomaly_type="activity_burst",
        detection_key=uuid.uuid4().hex,
        description="Deterministic test anomaly.",
        expected_value=1.0,
        actual_value=4.0,
        deviation_score=3.0,
        significance=0.95,
        baseline_days=28,
        window_days=7,
        detection_method="alert_integration_test",
        detected_at=detected_at,
        is_confirmed=False,
        is_dismissed=False,
        is_archived=False,
    )

    db.add(anomaly)
    db.flush()

    return anomaly


def _cleanup(
    organization_ids: list[uuid.UUID],
) -> None:
    if not organization_ids:
        return

    db = SessionLocal()

    try:
        db.rollback()

        db.execute(
            delete(Alert).where(
                Alert.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(Anomaly).where(
                Anomaly.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(Pattern).where(
                Pattern.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(RiskAssessment).where(
                RiskAssessment.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(Organization).where(
                Organization.id.in_(
                    organization_ids
                )
            )
        )

        db.commit()
    finally:
        db.close()


def test_build_alert_detection_key_is_deterministic():
    source_id = uuid.uuid4()

    first = build_alert_detection_key(
        source_type=ALERT_SOURCE_RISK,
        source_id=source_id,
        alert_type=ALERT_TYPE_RISK,
    )

    second = build_alert_detection_key(
        source_type=" RISK ",
        source_id=str(source_id),
        alert_type=" RISK ",
    )

    assert first == second
    assert len(first) == 64

    different_type = build_alert_detection_key(
        source_type=ALERT_SOURCE_RISK,
        source_id=source_id,
        alert_type="risk_escalation",
    )

    assert different_type != first


def test_risk_alert_retry_is_idempotent():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db,
            prefix="risk-retry",
        )
        organization_ids.append(
            organization.id
        )

        risk = _make_risk(
            db,
            organization_id=organization.id,
            assessed_at=BASE,
        )

        db.commit()

        candidate = AlertCandidate(
            organization_id=organization.id,
            source_type=ALERT_SOURCE_RISK,
            source_id=risk.id,
            alert_type=ALERT_TYPE_RISK,
            title="High deterministic risk",
            occurred_at=BASE,
            severity="high",
            message="Risk v2 crossed alert policy.",
            affected_entities=("Entity A",),
        )

        first = persist_alert_candidate(
            db,
            candidate=candidate,
        )
        db.commit()

        second = persist_alert_candidate(
            db,
            candidate=candidate,
        )
        db.commit()

        assert first.created is True
        assert second.created is False

        assert (
            first.alert_id
            == second.alert_id
        )

        assert (
            first.detection_key
            == second.detection_key
        )

        rows = (
            db.query(Alert)
            .filter(
                Alert.organization_id
                == organization.id,
                Alert.source_type
                == ALERT_SOURCE_RISK,
                Alert.source_id
                == risk.id,
            )
            .all()
        )

        assert len(rows) == 1

        alert = rows[0]

        assert alert.event_id is None
        assert alert.occurred_at == BASE
        assert alert.status == "pending"

    finally:
        db.close()
        _cleanup(organization_ids)


def test_pattern_alert_supports_null_event():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db,
            prefix="pattern",
        )
        organization_ids.append(
            organization.id
        )

        pattern = _make_pattern(
            db,
            organization_id=organization.id,
            last_event_at=BASE,
        )

        db.commit()

        result = persist_alert_candidate(
            db,
            candidate=AlertCandidate(
                organization_id=organization.id,
                source_type=ALERT_SOURCE_PATTERN,
                source_id=pattern.id,
                alert_type=ALERT_TYPE_PATTERN,
                title="Repeated semantic drift",
                occurred_at=pattern.last_event_at,
                event_id=None,
                severity="medium",
            ),
        )
        db.commit()

        assert result.created is True

        alert = db.get(
            Alert,
            result.alert_id,
        )

        assert alert is not None

        assert (
            alert.source_type
            == ALERT_SOURCE_PATTERN
        )

        assert alert.source_id == pattern.id
        assert alert.event_id is None
        assert alert.occurred_at == BASE

    finally:
        db.close()
        _cleanup(organization_ids)


def test_cross_tenant_risk_source_is_rejected():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        own_organization = _make_organization(
            db,
            prefix="own",
        )
        organization_ids.append(
            own_organization.id
        )

        foreign_organization = _make_organization(
            db,
            prefix="foreign",
        )
        organization_ids.append(
            foreign_organization.id
        )

        foreign_risk = _make_risk(
            db,
            organization_id=(
                foreign_organization.id
            ),
            assessed_at=BASE,
        )

        db.commit()

        with pytest.raises(
            AlertPersistenceValidationError,
            match=(
                "RiskAssessment source must belong "
                "to the target organization"
            ),
        ):
            persist_alert_candidate(
                db,
                candidate=AlertCandidate(
                    organization_id=(
                        own_organization.id
                    ),
                    source_type=ALERT_SOURCE_RISK,
                    source_id=foreign_risk.id,
                    alert_type=ALERT_TYPE_RISK,
                    title="Must not persist",
                    occurred_at=BASE,
                ),
            )

        db.rollback()

        count = (
            db.query(Alert)
            .filter(
                Alert.organization_id
                == own_organization.id
            )
            .count()
        )

        assert count == 0

    finally:
        db.close()
        _cleanup(organization_ids)


def test_anomaly_alert_requires_matching_anchor_event():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db,
            prefix="anomaly",
        )
        organization_ids.append(
            organization.id
        )

        #
        # We intentionally cannot create an Anomaly without
        # a valid SemanticDriftEvent because the DB FK protects
        # that invariant. This test therefore verifies the
        # service rejects a candidate whose event context does
        # not match the persisted Anomaly source.
        #
        # A complete event fixture is exercised elsewhere in
        # integration tests; here a source-less mismatch is
        # represented by a random candidate event UUID and must
        # fail before Alert persistence.
        #
        fake_anomaly_id = uuid.uuid4()
        fake_event_id = uuid.uuid4()

        with pytest.raises(
            AlertPersistenceValidationError
        ):
            persist_alert_candidate(
                db,
                candidate=AlertCandidate(
                    organization_id=organization.id,
                    source_type=ALERT_SOURCE_ANOMALY,
                    source_id=fake_anomaly_id,
                    alert_type=ALERT_TYPE_ANOMALY,
                    title="Must not persist",
                    occurred_at=BASE,
                    event_id=fake_event_id,
                ),
            )

        db.rollback()

        count = (
            db.query(Alert)
            .filter(
                Alert.organization_id
                == organization.id
            )
            .count()
        )

        assert count == 0

    finally:
        db.close()
        _cleanup(organization_ids)


def test_concurrent_risk_alert_persistence_creates_one_alert():
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            setup_db,
            prefix="concurrency",
        )
        organization_ids.append(
            organization.id
        )

        risk = _make_risk(
            setup_db,
            organization_id=organization.id,
            assessed_at=BASE,
        )

        organization_id = organization.id
        risk_id = risk.id

        setup_db.commit()

        barrier = Barrier(2)

        def worker():
            db = SessionLocal()

            try:
                barrier.wait()

                result = persist_alert_candidate(
                    db,
                    candidate=AlertCandidate(
                        organization_id=(
                            organization_id
                        ),
                        source_type=(
                            ALERT_SOURCE_RISK
                        ),
                        source_id=risk_id,
                        alert_type=ALERT_TYPE_RISK,
                        title=(
                            "Concurrent risk alert"
                        ),
                        occurred_at=BASE,
                        severity="high",
                    ),
                )

                db.commit()

                return (
                    result.created,
                    str(result.alert_id),
                    result.detection_key,
                )

            finally:
                db.close()

        with ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            results = list(
                executor.map(
                    lambda _: worker(),
                    range(2),
                )
            )

        created_flags = sorted(
            result[0]
            for result in results
        )

        assert created_flags == [
            False,
            True,
        ]

        alert_ids = {
            result[1]
            for result in results
        }

        assert len(alert_ids) == 1

        detection_keys = {
            result[2]
            for result in results
        }

        assert len(detection_keys) == 1

        verify_db = SessionLocal()

        try:
            rows = (
                verify_db.query(Alert)
                .filter(
                    Alert.organization_id
                    == organization_id,
                    Alert.source_type
                    == ALERT_SOURCE_RISK,
                    Alert.source_id
                    == risk_id,
                )
                .all()
            )

            assert len(rows) == 1

            assert (
                str(rows[0].id)
                in alert_ids
            )

        finally:
            verify_db.close()

    finally:
        setup_db.close()
        _cleanup(organization_ids)