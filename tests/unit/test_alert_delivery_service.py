from __future__ import annotations

import uuid
from datetime import datetime, timezone

from src.models.alert import Alert
from src.services.alert_delivery_service import (
    build_delivery_key,
    build_webhook_payload,
)


def _alert() -> Alert:
    alert = Alert()

    alert.id = uuid.UUID(
        "11111111-1111-1111-1111-111111111111"
    )
    alert.organization_id = uuid.UUID(
        "22222222-2222-2222-2222-222222222222"
    )
    alert.event_id = uuid.UUID(
        "33333333-3333-3333-3333-333333333333"
    )
    alert.source_type = "risk"
    alert.source_id = uuid.UUID(
        "44444444-4444-4444-4444-444444444444"
    )
    alert.alert_type = "risk"
    alert.title = "Risk increased"
    alert.message = "Material semantic risk detected."
    alert.severity = "high"
    alert.recommendation = "Review the affected source."
    alert.affected_entities = [
        "55555555-5555-5555-5555-555555555555",
    ]
    alert.occurred_at = datetime(
        2026,
        9,
        11,
        8,
        0,
        tzinfo=timezone.utc,
    )
    alert.created_at = datetime(
        2026,
        9,
        11,
        8,
        5,
        tzinfo=timezone.utc,
    )

    return alert


def test_delivery_key_is_deterministic() -> None:
    alert_id = uuid.UUID(
        "11111111-1111-1111-1111-111111111111"
    )
    destination_id = uuid.UUID(
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )

    first = build_delivery_key(
        alert_id=alert_id,
        destination_type="webhook",
        destination_id=destination_id,
    )

    second = build_delivery_key(
        alert_id=alert_id,
        destination_type="webhook",
        destination_id=destination_id,
    )

    assert first == second
    assert len(first) == 64


def test_delivery_key_normalizes_destination_type() -> None:
    alert_id = uuid.UUID(
        "11111111-1111-1111-1111-111111111111"
    )
    destination_id = uuid.UUID(
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )

    canonical = build_delivery_key(
        alert_id=alert_id,
        destination_type="webhook",
        destination_id=destination_id,
    )

    normalized = build_delivery_key(
        alert_id=alert_id,
        destination_type="  WEBHOOK  ",
        destination_id=destination_id,
    )

    assert canonical == normalized


def test_delivery_key_changes_for_different_destination() -> None:
    alert_id = uuid.UUID(
        "11111111-1111-1111-1111-111111111111"
    )

    first = build_delivery_key(
        alert_id=alert_id,
        destination_type="webhook",
        destination_id=uuid.UUID(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        ),
    )

    second = build_delivery_key(
        alert_id=alert_id,
        destination_type="webhook",
        destination_id=uuid.UUID(
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        ),
    )

    assert first != second


def test_webhook_payload_is_stable_alert_snapshot() -> None:
    alert = _alert()

    payload = build_webhook_payload(
        alert=alert,
    )

    assert payload == {
        "schema_version": 1,
        "event": "alert.created",
        "alert": {
            "id": (
                "11111111-1111-1111-1111-111111111111"
            ),
            "organization_id": (
                "22222222-2222-2222-2222-222222222222"
            ),
            "event_id": (
                "33333333-3333-3333-3333-333333333333"
            ),
            "source_type": "risk",
            "source_id": (
                "44444444-4444-4444-4444-444444444444"
            ),
            "alert_type": "risk",
            "title": "Risk increased",
            "message": (
                "Material semantic risk detected."
            ),
            "severity": "high",
            "recommendation": (
                "Review the affected source."
            ),
            "affected_entities": [
                "55555555-5555-5555-5555-555555555555",
            ],
            "occurred_at": (
                "2026-09-11T08:00:00+00:00"
            ),
            "created_at": (
                "2026-09-11T08:05:00+00:00"
            ),
        },
    }


def test_webhook_payload_supports_eventless_alert() -> None:
    alert = _alert()
    alert.event_id = None
    alert.source_id = None
    alert.affected_entities = None
    alert.occurred_at = None
    alert.created_at = None

    payload = build_webhook_payload(
        alert=alert,
    )

    body = payload["alert"]

    assert body["event_id"] is None
    assert body["source_id"] is None
    assert body["affected_entities"] == []
    assert body["occurred_at"] is None
    assert body["created_at"] is None