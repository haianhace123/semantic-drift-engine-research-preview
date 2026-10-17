from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.services.alert_delivery_fanout_service as fanout_module
from src.models.alert import (
    ALERT_SOURCE_ANOMALY,
    ALERT_SOURCE_PATTERN,
    ALERT_SOURCE_RISK,
)
from src.models.alert_delivery import (
    ALERT_DELIVERY_DESTINATION_WEBHOOK,
)
from src.services.alert_delivery_fanout_service import (
    AlertDeliveryFanoutError,
    fanout_alert_deliveries,
    reconcile_alert_deliveries,
    webhook_subscription_matches_alert,
)


def _alert(
    *,
    source_type: str,
    severity: str | None = None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        source_type=source_type,
        severity=severity,
    )


def _subscription(
    *,
    events,
    is_active: bool = True,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        events=events,
        is_active=is_active,
    )


@pytest.mark.parametrize(
    ("events", "severity", "expected"),
    [
        (["high_risk"], "high", True),
        (["high_risk"], "critical", True),
        (["high_risk"], "medium", False),
        (["high_risk"], "low", False),
        (["medium_risk"], "medium", True),
        (["medium_risk"], "high", False),
    ],
)
def test_risk_subscription_event_matching(
    events,
    severity,
    expected,
) -> None:
    alert = _alert(
        source_type=ALERT_SOURCE_RISK,
        severity=severity,
    )

    subscription = _subscription(
        events=events,
    )

    assert (
        webhook_subscription_matches_alert(
            subscription=subscription,
            alert=alert,
        )
        is expected
    )


def test_pattern_subscription_matches_pattern_alert() -> None:
    alert = _alert(
        source_type=ALERT_SOURCE_PATTERN,
    )

    subscription = _subscription(
        events=["pattern"],
    )

    assert webhook_subscription_matches_alert(
        subscription=subscription,
        alert=alert,
    )


def test_anomaly_subscription_matches_anomaly_alert() -> None:
    alert = _alert(
        source_type=ALERT_SOURCE_ANOMALY,
    )

    subscription = _subscription(
        events=["anomaly"],
    )

    assert webhook_subscription_matches_alert(
        subscription=subscription,
        alert=alert,
    )


@pytest.mark.parametrize(
    "events",
    [
        ["alert.created"],
        ["*"],
        [" ALERT.CREATED "],
    ],
)
def test_generic_subscription_matches_any_alert(
    events,
) -> None:
    alert = _alert(
        source_type=ALERT_SOURCE_PATTERN,
    )

    subscription = _subscription(
        events=events,
    )

    assert webhook_subscription_matches_alert(
        subscription=subscription,
        alert=alert,
    )


@pytest.mark.parametrize(
    "events",
    [
        None,
        [],
        ["   "],
    ],
)
def test_empty_subscription_events_fail_closed(
    events,
) -> None:
    alert = _alert(
        source_type=ALERT_SOURCE_RISK,
        severity="critical",
    )

    subscription = _subscription(
        events=events,
    )

    assert not webhook_subscription_matches_alert(
        subscription=subscription,
        alert=alert,
    )


def test_inactive_subscription_never_matches() -> None:
    alert = _alert(
        source_type=ALERT_SOURCE_RISK,
        severity="critical",
    )

    subscription = _subscription(
        events=["*"],
        is_active=False,
    )

    assert not webhook_subscription_matches_alert(
        subscription=subscription,
        alert=alert,
    )


def test_unknown_event_name_fails_closed() -> None:
    alert = _alert(
        source_type=ALERT_SOURCE_RISK,
        severity="high",
    )

    subscription = _subscription(
        events=["something_unknown"],
    )

    assert not webhook_subscription_matches_alert(
        subscription=subscription,
        alert=alert,
    )


def test_unknown_alert_source_type_fails_closed() -> None:
    alert = _alert(
        source_type="unknown",
        severity="high",
    )

    subscription = _subscription(
        events=["high_risk"],
    )

    assert not webhook_subscription_matches_alert(
        subscription=subscription,
        alert=alert,
    )


def test_fanout_materializes_only_matching_destinations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()

    alert = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        source_type=ALERT_SOURCE_RISK,
        severity="high",
    )

    matching_one = _subscription(
        events=["high_risk"],
    )

    matching_two = _subscription(
        events=["alert.created"],
    )

    nonmatching = _subscription(
        events=["pattern"],
    )

    load_alert = MagicMock(
        return_value=alert
    )

    load_webhooks = MagicMock(
        return_value=(
            matching_one,
            matching_two,
            nonmatching,
        )
    )

    persist = MagicMock()

    monkeypatch.setattr(
        fanout_module,
        "_load_alert",
        load_alert,
    )

    monkeypatch.setattr(
        fanout_module,
        "_load_active_webhooks",
        load_webhooks,
    )

    monkeypatch.setattr(
        fanout_module,
        "persist_alert_delivery",
        persist,
    )

    db = object()

    result = fanout_alert_deliveries(
        db,
        organization_id=organization_id,
        alert_id=alert.id,
    )

    assert result.organization_id == organization_id
    assert result.alert_id == alert.id
    assert result.matched_destination_count == 2
    assert result.delivery_count == 2

    load_alert.assert_called_once_with(
        db,
        organization_id=organization_id,
        alert_id=alert.id,
    )

    load_webhooks.assert_called_once_with(
        db,
        organization_id=organization_id,
    )

    assert persist.call_count == 2

    persist.assert_any_call(
        db,
        organization_id=organization_id,
        alert_id=alert.id,
        destination_type=(
            ALERT_DELIVERY_DESTINATION_WEBHOOK
        ),
        destination_id=matching_one.id,
    )

    persist.assert_any_call(
        db,
        organization_id=organization_id,
        alert_id=alert.id,
        destination_type=(
            ALERT_DELIVERY_DESTINATION_WEBHOOK
        ),
        destination_id=matching_two.id,
    )


def test_fanout_with_no_matching_destination_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()

    alert = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        source_type=ALERT_SOURCE_PATTERN,
        severity=None,
    )

    subscription = _subscription(
        events=["high_risk"],
    )

    monkeypatch.setattr(
        fanout_module,
        "_load_alert",
        MagicMock(
            return_value=alert
        ),
    )

    monkeypatch.setattr(
        fanout_module,
        "_load_active_webhooks",
        MagicMock(
            return_value=(
                subscription,
            )
        ),
    )

    persist = MagicMock()

    monkeypatch.setattr(
        fanout_module,
        "persist_alert_delivery",
        persist,
    )

    result = fanout_alert_deliveries(
        object(),
        organization_id=organization_id,
        alert_id=alert.id,
    )

    assert result.matched_destination_count == 0
    assert result.delivery_count == 0

    persist.assert_not_called()


def test_load_alert_rejects_cross_tenant_or_missing_alert() -> None:
    query = MagicMock()

    query.filter.return_value.one_or_none.return_value = (
        None
    )

    db = MagicMock()
    db.query.return_value = query

    with pytest.raises(
        AlertDeliveryFanoutError,
        match="does not exist",
    ):
        fanout_module._load_alert(
            db,
            organization_id=uuid.uuid4(),
            alert_id=uuid.uuid4(),
        )


def test_load_active_webhooks_returns_deterministic_order() -> None:
    db = MagicMock()
    query = MagicMock()

    expected = [
        SimpleNamespace(id=uuid.uuid4()),
        SimpleNamespace(id=uuid.uuid4()),
    ]

    (
        query.filter.return_value
        .order_by.return_value
        .all.return_value
    ) = expected

    db.query.return_value = query

    result = fanout_module._load_active_webhooks(
        db,
        organization_id=uuid.uuid4(),
    )

    assert result == tuple(expected)

    query.filter.assert_called_once()
    query.filter.return_value.order_by.assert_called_once()

def test_reconcile_alert_deliveries_materializes_matching_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()

    risk_alert = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        source_type=ALERT_SOURCE_RISK,
        severity="high",
    )

    pattern_alert = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        source_type=ALERT_SOURCE_PATTERN,
        severity=None,
    )

    high_risk_subscription = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        events=["high_risk"],
        is_active=True,
    )

    pattern_subscription = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        events=["pattern"],
        is_active=True,
    )

    persist = MagicMock()

    monkeypatch.setattr(
        fanout_module,
        "persist_alert_delivery",
        persist,
    )

    db = object()

    result = reconcile_alert_deliveries(
        db,
        organization_id=organization_id,
        alerts=(
            risk_alert,
            pattern_alert,
        ),
        subscriptions=(
            high_risk_subscription,
            pattern_subscription,
        ),
    )

    assert result.organization_id == organization_id
    assert result.alert_count == 2
    assert result.subscription_count == 2
    assert result.active_subscription_count == 2
    assert result.matched_destination_count == 2
    assert result.delivery_count == 2

    assert persist.call_count == 2

    persist.assert_any_call(
        db,
        organization_id=organization_id,
        alert_id=risk_alert.id,
        destination_type=(
            ALERT_DELIVERY_DESTINATION_WEBHOOK
        ),
        destination_id=high_risk_subscription.id,
    )

    persist.assert_any_call(
        db,
        organization_id=organization_id,
        alert_id=pattern_alert.id,
        destination_type=(
            ALERT_DELIVERY_DESTINATION_WEBHOOK
        ),
        destination_id=pattern_subscription.id,
    )


def test_reconcile_alert_deliveries_skips_inactive_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()

    alert = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        source_type=ALERT_SOURCE_PATTERN,
        severity=None,
    )

    active_subscription = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        events=["pattern"],
        is_active=True,
    )

    inactive_subscription = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        events=["pattern"],
        is_active=False,
    )

    persist = MagicMock()

    monkeypatch.setattr(
        fanout_module,
        "persist_alert_delivery",
        persist,
    )

    db = object()

    result = reconcile_alert_deliveries(
        db,
        organization_id=organization_id,
        alerts=(alert,),
        subscriptions=(
            active_subscription,
            inactive_subscription,
        ),
    )

    assert result.subscription_count == 2
    assert result.active_subscription_count == 1
    assert result.matched_destination_count == 1
    assert result.delivery_count == 1

    persist.assert_called_once_with(
        db,
        organization_id=organization_id,
        alert_id=alert.id,
        destination_type=(
            ALERT_DELIVERY_DESTINATION_WEBHOOK
        ),
        destination_id=active_subscription.id,
    )


@pytest.mark.parametrize(
    "cross_tenant_source",
    [
        "alert",
        "subscription",
    ],
)
def test_reconcile_alert_deliveries_rejects_cross_tenant_evidence(
    cross_tenant_source,
) -> None:
    organization_id = uuid.uuid4()

    alert = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        source_type=ALERT_SOURCE_RISK,
        severity="high",
    )

    subscription = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        events=["high_risk"],
        is_active=True,
    )

    if cross_tenant_source == "alert":
        alert.organization_id = uuid.uuid4()
    else:
        subscription.organization_id = uuid.uuid4()

    with pytest.raises(
        AlertDeliveryFanoutError,
        match="does not belong",
    ):
        reconcile_alert_deliveries(
            object(),
            organization_id=organization_id,
            alerts=(alert,),
            subscriptions=(subscription,),
        )
