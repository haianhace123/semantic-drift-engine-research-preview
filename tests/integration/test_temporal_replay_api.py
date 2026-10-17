from __future__ import annotations

import uuid

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

import src.main as main_module

from src.api import temporal_routes
from src.core.security import get_current_user
from src.main import app
from src.services.temporal_replay_service import (
    ReplayEvent,
    ReplayGraphEntity,
    ReplayGraphState,
    ReplayRiskAssessment,
    ReplaySnapshot,
    ReplayTopologyEntity,
    ReplayTopologyState,
    TemporalReplayState,
)


client = TestClient(
    app
)


def _utc(
    hour: int,
) -> datetime:
    return datetime(
        2026,
        8,
        30,
        hour,
        0,
        0,
        tzinfo=timezone.utc,
    )


def _fake_user(
    organization_id: uuid.UUID,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        email="temporal-replay-api@example.com",
    )


def _fake_db():
    yield object()

def _allow_auth_middleware(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "verify_token",
        lambda token: {
            "sub": str(uuid.uuid4()),
        },
    )


def _replay_fixture(
    *,
    organization_id: uuid.UUID,
    cursor: datetime,
) -> TemporalReplayState:
    entity_id = uuid.uuid4()
    page_id = uuid.uuid4()
    old_snapshot_id = uuid.uuid4()
    new_snapshot_id = uuid.uuid4()
    event_id = uuid.uuid4()

    return TemporalReplayState(
        organization_id=organization_id,
        cursor=cursor,
        snapshots=(
            ReplaySnapshot(
                snapshot_id=new_snapshot_id,
                monitored_page_id=page_id,
                entity_id=entity_id,
                content_hash="snapshot-hash",
                crawled_at=_utc(2),
                created_at=_utc(2),
            ),
        ),
        events=(
            ReplayEvent(
                event_id=event_id,
                monitored_page_id=page_id,
                entity_id=entity_id,
                old_snapshot_id=old_snapshot_id,
                new_snapshot_id=new_snapshot_id,
                detected_at=_utc(2),
                risk_level="high",
                semantic_magnitude=0.8,
            ),
        ),
        graph=ReplayGraphState(
            observed_at=_utc(2),
            created_at=_utc(3),
            semantic_drift_event_id=event_id,
            entity_count=1,
            active_edge_count=0,
            entities=(
                ReplayGraphEntity(
                    entity_id=entity_id,
                    in_degree=0,
                    out_degree=0,
                    degree=0,
                    degree_centrality=0.0,
                    downstream_reach=0,
                    downstream_reach_ratio=0.0,
                ),
            ),
        ),
        topology=ReplayTopologyState(
            observed_at=_utc(2),
            created_at=_utc(3),
            semantic_drift_event_id=event_id,
            previous_observed_at=None,
            entities=(
                ReplayTopologyEntity(
                    entity_id=entity_id,
                    classification="stable",
                    severity="none",
                    shift_score=0.0,
                    degree_delta=0,
                    degree_centrality_delta=0.0,
                    downstream_reach_delta=0,
                    downstream_reach_ratio_delta=0.0,
                ),
            ),
        ),
        risk_assessments=(
            ReplayRiskAssessment(
                assessment_id=uuid.uuid4(),
                semantic_drift_event_id=event_id,
                risk_version="deterministic_v2",
                assessed_at=_utc(2),
                created_at=_utc(4),
                score=0.72,
                topology_score=0.0,
                topology_contribution=0.0,
                has_topology_signal=False,
                has_previous_topology_state=False,
            ),
        ),
    )


def test_temporal_replay_api_serializes_replay_state(
    monkeypatch,
):
    _allow_auth_middleware(
        monkeypatch
    )

    organization_id = uuid.uuid4()

    captured = {}

    def fake_replay(
        db,
        *,
        organization_id,
        cursor,
    ):
        captured["db"] = db
        captured[
            "organization_id"
        ] = organization_id
        captured["cursor"] = cursor

        return _replay_fixture(
            organization_id=organization_id,
            cursor=cursor,
        )

    app.dependency_overrides[
        get_current_user
    ] = lambda: _fake_user(
        organization_id
    )

    app.dependency_overrides[
        temporal_routes.get_db
    ] = _fake_db

    monkeypatch.setattr(
        temporal_routes,
        "replay_organization_at",
        fake_replay,
    )

    try:
        response = client.get(
            "/api/temporal/replay",
            headers={
                "Authorization":
                    "Bearer temporal-replay-test-token",
            },
            params={
                "at":
                    "2026-08-30T05:00:00Z",
            },
        )

        assert response.status_code == 200

        payload = response.json()

        assert (
            captured["organization_id"]
            == organization_id
        )

        assert (
            captured["cursor"]
            == _utc(5)
        )

        assert (
            payload["organization_id"]
            == str(organization_id)
        )

        assert (
            payload["at"]
            == "2026-08-30T05:00:00+00:00"
        )

        assert (
            payload["snapshot_count"]
            == 1
        )

        assert (
            payload["event_count"]
            == 1
        )

        assert (
            payload[
                "risk_assessment_count"
            ]
            == 1
        )

        assert len(
            payload["snapshots"]
        ) == 1

        assert len(
            payload["events"]
        ) == 1

        assert payload["graph"] is not None

        assert (
            payload["topology"]
            is not None
        )

        assert len(
            payload["risk_assessments"]
        ) == 1

        assert (
            payload[
                "risk_assessments"
            ][0]["risk_version"]
            == "deterministic_v2"
        )

    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

        app.dependency_overrides.pop(
            temporal_routes.get_db,
            None,
        )


def test_temporal_replay_api_does_not_expose_raw_content(
    monkeypatch,
):
    _allow_auth_middleware(
        monkeypatch
    )

    organization_id = uuid.uuid4()

    app.dependency_overrides[
        get_current_user
    ] = lambda: _fake_user(
        organization_id
    )

    app.dependency_overrides[
        temporal_routes.get_db
    ] = _fake_db

    monkeypatch.setattr(
        temporal_routes,
        "replay_organization_at",
        lambda db, *, organization_id, cursor:
            _replay_fixture(
                organization_id=organization_id,
                cursor=cursor,
            ),
    )

    try:
        response = client.get(
            "/api/temporal/replay",
            headers={
                "Authorization":
                    "Bearer temporal-replay-test-token",
            },
            params={
                "at":
                    "2026-08-30T05:00:00Z",
            },
        )

        assert response.status_code == 200

        serialized = response.text

        assert "raw_content" not in serialized
        assert "raw_content_path" not in serialized
        assert "embedding" not in serialized

        snapshot = (
            response.json()[
                "snapshots"
            ][0]
        )

        assert set(snapshot) == {
            "snapshot_id",
            "monitored_page_id",
            "entity_id",
            "content_hash",
            "crawled_at",
            "created_at",
        }

    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

        app.dependency_overrides.pop(
            temporal_routes.get_db,
            None,
        )


def test_temporal_replay_api_uses_authenticated_tenant(
    monkeypatch,
):
    _allow_auth_middleware(
        monkeypatch
    )

    authenticated_org_id = uuid.uuid4()

    captured = {}

    def fake_replay(
        db,
        *,
        organization_id,
        cursor,
    ):
        captured[
            "organization_id"
        ] = organization_id

        return _replay_fixture(
            organization_id=organization_id,
            cursor=cursor,
        )

    app.dependency_overrides[
        get_current_user
    ] = lambda: _fake_user(
        authenticated_org_id
    )

    app.dependency_overrides[
        temporal_routes.get_db
    ] = _fake_db

    monkeypatch.setattr(
        temporal_routes,
        "replay_organization_at",
        fake_replay,
    )

    try:
        attacker_org_id = uuid.uuid4()

        response = client.get(
            "/api/temporal/replay",
            headers={
                "Authorization":
                    "Bearer temporal-replay-test-token",
            },
            params={
                "at":
                    "2026-08-30T05:00:00Z",

                "organization_id":
                    str(attacker_org_id),
            },
        )

        assert response.status_code == 200

        assert (
            captured["organization_id"]
            == authenticated_org_id
        )

        assert (
            response.json()[
                "organization_id"
            ]
            == str(
                authenticated_org_id
            )
        )

    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

        app.dependency_overrides.pop(
            temporal_routes.get_db,
            None,
        )


def test_temporal_replay_api_rejects_invalid_cursor(
    monkeypatch,
):
    _allow_auth_middleware(
        monkeypatch
    )

    organization_id = uuid.uuid4()

    app.dependency_overrides[
        get_current_user
    ] = lambda: _fake_user(
        organization_id
    )

    app.dependency_overrides[
        temporal_routes.get_db
    ] = _fake_db

    try:
        response = client.get(
            "/api/temporal/replay",
            headers={
                "Authorization":
                    "Bearer temporal-replay-test-token",
            },
            params={
                "at":
                    "definitely-not-a-date",
            },
        )

        assert response.status_code in (
            400,
            422,
        )

    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

        app.dependency_overrides.pop(
            temporal_routes.get_db,
            None,
        )