from __future__ import annotations

import uuid

from datetime import (
    datetime,
    timezone,
)

from types import SimpleNamespace

from fastapi.testclient import TestClient

import src.main as main_module

from src.api import temporal_routes
from src.core.security import get_current_user
from src.main import app

from src.services.what_you_missed_evidence_service import (
    WhatYouMissedEvidenceValidationError,
)

from src.services.what_you_missed_explanation_service import (
    ExplanationReason,
    WhatYouMissedExplanation,
    WhatYouMissedItem,
)


client = TestClient(
    app
)


def _utc(
    day: int,
    hour: int = 0,
) -> datetime:
    return datetime(
        2026,
        9,
        day,
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
        email=(
            "what-you-missed-api@example.com"
        ),
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
            "sub": str(
                uuid.uuid4()
            ),
        },
    )


def _explanation_fixture(
    *,
    organization_id: uuid.UUID,
    since: datetime,
    until: datetime,
) -> WhatYouMissedExplanation:
    event_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    page_id = uuid.uuid4()
    pattern_id = uuid.uuid4()
    anomaly_id = uuid.uuid4()
    topology_shift_id = uuid.uuid4()

    item = WhatYouMissedItem(
        event_id=event_id,
        entity_id=entity_id,
        monitored_page_id=page_id,
        entity_name="OpenAI",
        page_url=(
            "https://example.com/pricing"
        ),
        page_type="pricing",
        occurred_at=_utc(
            2,
            6,
        ),
        importance_score=0.82,
        importance_level="high",
        headline=(
            "High-risk change detected "
            "for OpenAI"
        ),
        summary=(
            "Pricing terms changed."
        ),
        risk_score=0.82,
        semantic_magnitude=0.78,
        impact_score=80,
        pattern_ids=(
            pattern_id,
        ),
        anomaly_ids=(
            anomaly_id,
        ),
        topology_shift_ids=(
            topology_shift_id,
        ),
        reasons=(
            ExplanationReason(
                code="HIGH_RISK",
                weight=0.82,
                text=(
                    "Risk v2 đánh giá thay đổi "
                    "này có mức rủi ro cao."
                ),
            ),
            ExplanationReason(
                code=(
                    "TOPOLOGY_CENTRALITY_GAIN"
                ),
                weight=0.6,
                text=(
                    "Entity gained graph "
                    "centrality."
                ),
            ),
        ),
    )

    return WhatYouMissedExplanation(
        organization_id=organization_id,
        since=since,
        until=until,
        event_count=1,
        explained_event_count=1,
        items=(
            item,
        ),
    )


def test_what_you_missed_api_serializes_read_model(
    monkeypatch,
):
    _allow_auth_middleware(
        monkeypatch
    )

    organization_id = uuid.uuid4()

    captured = {}

    def fake_get(
        db,
        *,
        organization_id,
        since,
        until,
        limit,
    ):
        captured["db"] = db
        captured[
            "organization_id"
        ] = organization_id
        captured["since"] = since
        captured["until"] = until
        captured["limit"] = limit

        return _explanation_fixture(
            organization_id=(
                organization_id
            ),
            since=since,
            until=until,
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
        "get_what_you_missed",
        fake_get,
    )

    try:
        response = client.get(
            (
                "/api/temporal/"
                "what-you-missed"
            ),
            headers={
                "Authorization": (
                    "Bearer "
                    "what-you-missed-test-token"
                ),
            },
            params={
                "since":
                    "2026-09-01T00:00:00Z",
                "until":
                    "2026-09-02T12:00:00Z",
                "limit":
                    25,
            },
        )

        assert response.status_code == 200

        payload = response.json()

        assert (
            captured["organization_id"]
            == organization_id
        )

        assert (
            captured["since"]
            == _utc(1)
        )

        assert (
            captured["until"]
            == _utc(
                2,
                12,
            )
        )

        assert (
            captured["limit"]
            == 25
        )

        assert (
            payload["organization_id"]
            == str(
                organization_id
            )
        )

        assert (
            payload["since"]
            == (
                "2026-09-01"
                "T00:00:00+00:00"
            )
        )

        assert (
            payload["until"]
            == (
                "2026-09-02"
                "T12:00:00+00:00"
            )
        )

        assert (
            payload["event_count"]
            == 1
        )

        assert (
            payload[
                "explained_event_count"
            ]
            == 1
        )

        assert len(
            payload["items"]
        ) == 1

        item = payload["items"][0]

        assert (
            item["importance_score"]
            == 0.82
        )

        assert (
            item["risk_score"]
            == 0.82
        )

        assert (
            item["importance_level"]
            == "high"
        )

        assert (
            item["entity_name"]
            == "OpenAI"
        )

        assert len(
            item["pattern_ids"]
        ) == 1

        assert len(
            item["anomaly_ids"]
        ) == 1

        assert len(
            item["topology_shift_ids"]
        ) == 1

        assert (
            item["reasons"][0]["code"]
            == "HIGH_RISK"
        )

        assert (
            item["reasons"][0]["weight"]
            == 0.82
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


def test_what_you_missed_api_does_not_expose_sensitive_snapshot_data(
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
        "get_what_you_missed",
        lambda db, **kwargs:
            _explanation_fixture(
                organization_id=(
                    kwargs[
                        "organization_id"
                    ]
                ),
                since=kwargs["since"],
                until=kwargs["until"],
            ),
    )

    try:
        response = client.get(
            (
                "/api/temporal/"
                "what-you-missed"
            ),
            headers={
                "Authorization": (
                    "Bearer "
                    "what-you-missed-test-token"
                ),
            },
            params={
                "since":
                    "2026-09-01",
                "until":
                    "2026-09-02",
            },
        )

        assert response.status_code == 200

        serialized = response.text

        assert "raw_content" not in serialized
        assert (
            "raw_content_path"
            not in serialized
        )
        assert "embedding" not in serialized
        assert "content_hash" not in serialized

    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

        app.dependency_overrides.pop(
            temporal_routes.get_db,
            None,
        )


def test_what_you_missed_api_uses_authenticated_tenant(
    monkeypatch,
):
    _allow_auth_middleware(
        monkeypatch
    )

    authenticated_org_id = (
        uuid.uuid4()
    )

    captured = {}

    def fake_get(
        db,
        *,
        organization_id,
        since,
        until,
        limit,
    ):
        captured[
            "organization_id"
        ] = organization_id

        return _explanation_fixture(
            organization_id=(
                organization_id
            ),
            since=since,
            until=until,
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
        "get_what_you_missed",
        fake_get,
    )

    try:
        attacker_org_id = uuid.uuid4()

        response = client.get(
            (
                "/api/temporal/"
                "what-you-missed"
            ),
            headers={
                "Authorization": (
                    "Bearer "
                    "what-you-missed-test-token"
                ),
            },
            params={
                "since":
                    "2026-09-01",
                "until":
                    "2026-09-02",
                "organization_id":
                    str(
                        attacker_org_id
                    ),
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


def test_what_you_missed_api_rejects_invalid_datetime(
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
            (
                "/api/temporal/"
                "what-you-missed"
            ),
            headers={
                "Authorization": (
                    "Bearer "
                    "what-you-missed-test-token"
                ),
            },
            params={
                "since":
                    "definitely-not-a-date",
                "until":
                    "2026-09-02",
            },
        )

        assert response.status_code == 422

    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

        app.dependency_overrides.pop(
            temporal_routes.get_db,
            None,
        )


def test_what_you_missed_api_rejects_invalid_window(
    monkeypatch,
):
    _allow_auth_middleware(
        monkeypatch
    )

    organization_id = uuid.uuid4()

    def fake_get(
        db,
        **kwargs,
    ):
        raise (
            WhatYouMissedEvidenceValidationError(
                "until must be after since"
            )
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
        "get_what_you_missed",
        fake_get,
    )

    try:
        response = client.get(
            (
                "/api/temporal/"
                "what-you-missed"
            ),
            headers={
                "Authorization": (
                    "Bearer "
                    "what-you-missed-test-token"
                ),
            },
            params={
                "since":
                    "2026-09-02T12:00:00Z",
                "until":
                    "2026-09-01T12:00:00Z",
            },
        )

        assert response.status_code == 422

        assert (
            "until must be after since"
            in response.json()["detail"]
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


def test_what_you_missed_api_validates_limit(
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
        for invalid_limit in (
            0,
            101,
        ):
            response = client.get(
                (
                    "/api/temporal/"
                    "what-you-missed"
                ),
                headers={
                    "Authorization": (
                        "Bearer "
                        "what-you-missed-test-token"
                    ),
                },
                params={
                    "since":
                        "2026-09-01",
                    "until":
                        "2026-09-02",
                    "limit":
                        invalid_limit,
                },
            )

            assert (
                response.status_code
                == 422
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