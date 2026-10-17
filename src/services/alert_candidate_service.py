"""
Deterministic Alert candidate and Watchlist policy service.

This module translates persisted intelligence sources into AlertCandidate
objects without persisting Alerts or attempting external delivery.

Responsibilities:
- canonical Risk v2 severity classification;
- deterministic Watchlist scope matching;
- Risk alert policy;
- Pattern alert policy;
- source-to-AlertCandidate presentation mapping;
- fail-closed Anomaly policy until the Watchlist schema explicitly supports
  anomaly alerts.

This module intentionally performs:
- no database queries;
- no database writes;
- no commits or rollbacks;
- no Slack/Webhook/API calls.

Database source discovery and durable pipeline checkpointing belong to the
Alert execution/orchestration layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import uuid

from src.models.alert import (
    ALERT_SOURCE_ANOMALY,
    ALERT_SOURCE_PATTERN,
    ALERT_SOURCE_RISK,
)
from src.models.anomaly import Anomaly
from src.models.monitored_page import MonitoredPage
from src.models.pattern import Pattern
from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import SemanticDriftEvent
from src.models.watchlist import Watchlist
from src.services.alert_service import (
    ALERT_TYPE_ANOMALY,
    ALERT_TYPE_PATTERN,
    ALERT_TYPE_RISK,
    AlertCandidate,
)
from src.services.risk_classification_service import (
    RiskSeverity,
    classify_risk_score,
)
from src.services.risk_assessment_service import (
    RISK_VERSION_V2,
)


class AlertCandidatePolicyError(
    ValueError
):
    """Raised when source evidence is invalid for candidate generation."""


@dataclass(frozen=True)
class AlertScope:
    """
    Normalized source scope used for Watchlist matching.

    Watchlist UUID collections are stored as canonical UUID strings.
    """

    entity_ids: tuple[str, ...] = ()
    page_ids: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()


def _canonical_uuid_string(
    value: object,
) -> str | None:
    if value is None:
        return None

    try:
        return str(
            uuid.UUID(
                str(
                    value
                )
            )
        )
    except (
        TypeError,
        ValueError,
        AttributeError,
    ):
        return None


def _normalized_uuid_strings(
    values: Iterable[object] | None,
) -> tuple[str, ...]:
    if not values:
        return ()

    normalized: set[str] = set()

    for value in values:
        canonical = (
            _canonical_uuid_string(
                value
            )
        )

        if canonical is not None:
            normalized.add(
                canonical
            )

    return tuple(
        sorted(
            normalized
        )
    )


def _normalized_categories(
    values: Iterable[object] | None,
) -> tuple[str, ...]:
    if not values:
        return ()

    normalized = {
        str(
            value
        ).strip().lower()
        for value in values
        if (
            value is not None
            and str(
                value
            ).strip()
        )
    }

    return tuple(
        sorted(
            normalized
        )
    )


def _same_uuid(
    left: object,
    right: object,
) -> bool:
    left_value = (
        _canonical_uuid_string(
            left
        )
    )

    right_value = (
        _canonical_uuid_string(
            right
        )
    )

    return (
        left_value is not None
        and right_value is not None
        and left_value == right_value
    )


def _require_same_organization(
    *,
    expected_organization_id: object,
    source_organization_id: object,
    source_name: str,
) -> None:
    if not _same_uuid(
        expected_organization_id,
        source_organization_id,
    ):
        raise AlertCandidatePolicyError(
            f"{source_name} does not belong to organization."
        )


def _validate_watchlists(
    *,
    organization_id: object,
    watchlists: Iterable[Watchlist],
) -> tuple[Watchlist, ...]:
    normalized = tuple(
        watchlists
    )

    for watchlist in normalized:
        _require_same_organization(
            expected_organization_id=organization_id,
            source_organization_id=watchlist.organization_id,
            source_name="Watchlist",
        )

    return normalized


def _active_watchlists(
    watchlists: Iterable[Watchlist],
) -> tuple[Watchlist, ...]:
    return tuple(
        watchlist
        for watchlist in watchlists
        if (
            bool(
                watchlist.is_active
            )
            and not bool(
                watchlist.is_archived
            )
        )
    )


def _watchlist_scope(
    watchlist: Watchlist,
) -> AlertScope:
    return AlertScope(
        entity_ids=(
            _normalized_uuid_strings(
                watchlist.entity_ids
                or ()
            )
        ),
        page_ids=(
            _normalized_uuid_strings(
                watchlist.page_ids
                or ()
            )
        ),
        categories=(
            _normalized_categories(
                watchlist.categories
                or ()
            )
        ),
    )


def _is_global_scope(
    scope: AlertScope,
) -> bool:
    return not (
        scope.entity_ids
        or scope.page_ids
        or scope.categories
    )


def _scope_matches(
    *,
    watchlist: Watchlist,
    source_scope: AlertScope,
) -> bool:
    """
    Match one source against one Watchlist.

    Empty Watchlist scope means organization-wide.

    Entity, page, and category scopes form a union. Matching any configured
    scope dimension is sufficient.
    """
    watchlist_scope = (
        _watchlist_scope(
            watchlist
        )
    )

    if _is_global_scope(
        watchlist_scope
    ):
        return True

    if (
        set(
            watchlist_scope.entity_ids
        )
        & set(
            source_scope.entity_ids
        )
    ):
        return True

    if (
        set(
            watchlist_scope.page_ids
        )
        & set(
            source_scope.page_ids
        )
    ):
        return True

    if (
        set(
            watchlist_scope.categories
        )
        & set(
            source_scope.categories
        )
    ):
        return True

    return False


def _event_scope(
    *,
    event: SemanticDriftEvent,
    monitored_page: MonitoredPage,
) -> AlertScope:
    categories: tuple[str, ...] = ()

    if event.category:
        categories = (
            _normalized_categories(
                (
                    event.category,
                )
            )
        )

    return AlertScope(
        entity_ids=(
            _normalized_uuid_strings(
                (
                    monitored_page.entity_id,
                )
            )
        ),
        page_ids=(
            _normalized_uuid_strings(
                (
                    monitored_page.id,
                )
            )
        ),
        categories=categories,
    )


def _pattern_scope(
    pattern: Pattern,
) -> AlertScope:
    """
    Pattern.entities is currently persisted as canonical entity UUID strings.

    Pattern.categories is supported here even though current persistence
    writes an empty list. This keeps policy behavior correct if category
    evidence is populated later without changing matching semantics.
    """
    return AlertScope(
        entity_ids=(
            _normalized_uuid_strings(
                pattern.entities
                or ()
            )
        ),
        page_ids=(),
        categories=(
            _normalized_categories(
                pattern.categories
                or ()
            )
        ),
    )


def _validate_event_page(
    *,
    organization_id: object,
    event: SemanticDriftEvent,
    monitored_page: MonitoredPage,
) -> None:
    _require_same_organization(
        expected_organization_id=organization_id,
        source_organization_id=event.organization_id,
        source_name="SemanticDriftEvent",
    )

    _require_same_organization(
        expected_organization_id=organization_id,
        source_organization_id=monitored_page.organization_id,
        source_name="MonitoredPage",
    )

    if not _same_uuid(
        event.monitored_page_id,
        monitored_page.id,
    ):
        raise AlertCandidatePolicyError(
            "SemanticDriftEvent monitored_page_id does not "
            "match MonitoredPage."
        )


def _risk_watchlist_enabled(
    *,
    watchlist: Watchlist,
    severity: RiskSeverity,
) -> bool:
    if severity in (
        "critical",
        "high",
    ):
        return bool(
            watchlist.alert_on_high_risk
        )

    if severity == "medium":
        return bool(
            watchlist.alert_on_medium_risk
        )

    return False


def _risk_default_enabled(
    severity: RiskSeverity,
) -> bool:
    """
    Preserve the existing default behavior when no active Watchlist exists.

    High and critical signals alert by default. Medium and low do not.
    """
    return severity in (
        "critical",
        "high",
    )


def _risk_policy_allows(
    *,
    severity: RiskSeverity,
    source_scope: AlertScope,
    watchlists: Iterable[Watchlist],
) -> bool:
    if severity == "low":
        return False

    active = (
        _active_watchlists(
            watchlists
        )
    )

    if not active:
        return _risk_default_enabled(
            severity
        )

    return any(
        (
            _scope_matches(
                watchlist=watchlist,
                source_scope=source_scope,
            )
            and _risk_watchlist_enabled(
                watchlist=watchlist,
                severity=severity,
            )
        )
        for watchlist in active
    )


def _pattern_policy_allows(
    *,
    source_scope: AlertScope,
    watchlists: Iterable[Watchlist],
) -> bool:
    """
    Pattern alerts are enabled by default when no active Watchlist exists.

    Once active Watchlists exist, at least one matching Watchlist must have
    alert_on_pattern enabled.
    """
    active = (
        _active_watchlists(
            watchlists
        )
    )

    if not active:
        return True

    return any(
        (
            _scope_matches(
                watchlist=watchlist,
                source_scope=source_scope,
            )
            and bool(
                watchlist.alert_on_pattern
            )
        )
        for watchlist in active
    )

def _anomaly_policy_allows(
    *,
    source_scope: AlertScope,
    watchlists: Iterable[Watchlist],
) -> bool:
    """
    Anomaly alerts are enabled by default when no active Watchlist exists.

    Once active Watchlists exist, at least one matching Watchlist must have
    alert_on_anomaly enabled.
    """
    active = (
        _active_watchlists(
            watchlists
        )
    )

    if not active:
        return True

    return any(
        (
            _scope_matches(
                watchlist=watchlist,
                source_scope=source_scope,
            )
            and bool(
                watchlist.alert_on_anomaly
            )
        )
        for watchlist in active
    )

def build_risk_alert_candidate(
    *,
    organization_id: object,
    assessment: RiskAssessment,
    event: SemanticDriftEvent | None,
    monitored_page: MonitoredPage | None,
    watchlists: Iterable[Watchlist] = (),
) -> AlertCandidate | None:
    """
    Build a Risk v2 AlertCandidate when policy allows it.

    Eventless RiskAssessments remain supported because the persistence model
    permits semantic_drift_event_id=NULL. Scoped Watchlists cannot match such
    an assessment, while a global Watchlist can.
    """
    _require_same_organization(
        expected_organization_id=organization_id,
        source_organization_id=assessment.organization_id,
        source_name="RiskAssessment",
    )

    validated_watchlists = (
        _validate_watchlists(
            organization_id=organization_id,
            watchlists=watchlists,
        )
    )

    if (
        assessment.risk_version
        != RISK_VERSION_V2
    ):
        return None

    assessment_event_id = (
        assessment.semantic_drift_event_id
    )

    if assessment_event_id is None:
        if event is not None:
            raise AlertCandidatePolicyError(
                "RiskAssessment has no semantic_drift_event_id "
                "but SemanticDriftEvent was provided."
            )

        if monitored_page is not None:
            raise AlertCandidatePolicyError(
                "Eventless RiskAssessment must not provide "
                "MonitoredPage."
            )

        source_scope = (
            AlertScope()
        )

        event_id = None

        occurred_at = (
            assessment.assessed_at
        )

        affected_entities: tuple[str, ...] = ()

        event_summary = None

    else:
        if event is None:
            raise AlertCandidatePolicyError(
                "RiskAssessment references a SemanticDriftEvent "
                "but no event was provided."
            )

        if monitored_page is None:
            raise AlertCandidatePolicyError(
                "Event-backed RiskAssessment requires MonitoredPage."
            )

        if not _same_uuid(
            assessment_event_id,
            event.id,
        ):
            raise AlertCandidatePolicyError(
                "RiskAssessment semantic_drift_event_id does not "
                "match SemanticDriftEvent."
            )

        _validate_event_page(
            organization_id=organization_id,
            event=event,
            monitored_page=monitored_page,
        )

        # An assessment may have been persisted before upstream readiness
        # checks existed. Preserve that historical record, but do not turn
        # incomplete semantic/vector evidence into a new risk alert.
        if (
            getattr(event, "analysis_status", None) != "completed"
            or getattr(event, "vector_analysis_status", None) != "completed"
        ):
            return None

        source_scope = (
            _event_scope(
                event=event,
                monitored_page=monitored_page,
            )
        )

        event_id = event.id

        occurred_at = (
            event.detected_at
        )

        affected_entities = (
            source_scope.entity_ids
        )

        event_summary = (
            event.summary_text.strip()
            if (
                event.summary_text
                and event.summary_text.strip()
            )
            else None
        )

    severity = (
        classify_risk_score(
            assessment.score
        )
    )

    if not _risk_policy_allows(
        severity=severity,
        source_scope=source_scope,
        watchlists=validated_watchlists,
    ):
        return None

    score_percent = (
        round(
            float(
                assessment.score
            )
            * 100.0,
            2,
        )
    )

    title = (
        f"{severity.capitalize()} risk semantic drift detected"
    )

    message = (
        event_summary
        or (
            "Deterministic Risk v2 assessment "
            f"scored {score_percent:.2f}%."
        )
    )

    return AlertCandidate(
        organization_id=(
            assessment.organization_id
        ),
        source_type=ALERT_SOURCE_RISK,
        source_id=assessment.id,
        alert_type=ALERT_TYPE_RISK,
        title=title,
        occurred_at=occurred_at,
        event_id=event_id,
        message=message,
        severity=severity,
        affected_entities=affected_entities,
    )


def build_pattern_alert_candidate(
    *,
    organization_id: object,
    pattern: Pattern,
    watchlists: Iterable[Watchlist] = (),
) -> AlertCandidate | None:
    """
    Build a Pattern AlertCandidate when current Watchlist policy allows it.

    Pattern Alerts are intentionally eventless at Alert persistence level.
    Pattern.event_ids remain evidence owned by Pattern itself.
    """
    _require_same_organization(
        expected_organization_id=organization_id,
        source_organization_id=pattern.organization_id,
        source_name="Pattern",
    )

    validated_watchlists = (
        _validate_watchlists(
            organization_id=organization_id,
            watchlists=watchlists,
        )
    )

    if (
        not bool(
            pattern.is_active
        )
        or bool(
            pattern.is_archived
        )
    ):
        return None

    source_scope = (
        _pattern_scope(
            pattern
        )
    )

    if not _pattern_policy_allows(
        source_scope=source_scope,
        watchlists=validated_watchlists,
    ):
        return None

    name = (
        pattern.name.strip()
        if (
            pattern.name
            and pattern.name.strip()
        )
        else "Semantic drift pattern"
    )

    summary = (
        pattern.summary.strip()
        if (
            pattern.summary
            and pattern.summary.strip()
        )
        else None
    )

    description = (
        pattern.description.strip()
        if (
            pattern.description
            and pattern.description.strip()
        )
        else None
    )

    message = (
        summary
        or description
        or (
            f"Pattern detected across "
            f"{int(pattern.event_count or 0)} drift event(s)."
        )
    )

    occurred_at = (
        pattern.last_event_at
        or pattern.detected_at
    )

    return AlertCandidate(
        organization_id=(
            pattern.organization_id
        ),
        source_type=ALERT_SOURCE_PATTERN,
        source_id=pattern.id,
        alert_type=ALERT_TYPE_PATTERN,
        title=f"Pattern detected: {name}",
        occurred_at=occurred_at,
        event_id=None,
        message=message,
        severity=None,
        affected_entities=(
            source_scope.entity_ids
        ),
    )


def build_anomaly_alert_candidate(
    *,
    organization_id: object,
    anomaly: Anomaly,
    event: SemanticDriftEvent,
    monitored_page: MonitoredPage,
    watchlists: Iterable[Watchlist] = (),
) -> AlertCandidate | None:
    """
    Build an Anomaly AlertCandidate when current Watchlist policy allows it.

    Anomaly significance remains source evidence only. It is not mapped to
    Alert severity because no canonical anomaly severity thresholds exist.
    """
    _require_same_organization(
        expected_organization_id=organization_id,
        source_organization_id=anomaly.organization_id,
        source_name="Anomaly",
    )

    validated_watchlists = (
        _validate_watchlists(
            organization_id=organization_id,
            watchlists=watchlists,
        )
    )

    if not _same_uuid(
        anomaly.event_id,
        event.id,
    ):
        raise AlertCandidatePolicyError(
            "Anomaly event_id does not match SemanticDriftEvent."
        )

    _validate_event_page(
        organization_id=organization_id,
        event=event,
        monitored_page=monitored_page,
    )

    if (
        bool(
            anomaly.is_archived
        )
        or bool(
            anomaly.is_dismissed
        )
    ):
        return None

    source_scope = (
        _event_scope(
            event=event,
            monitored_page=monitored_page,
        )
    )

    if not _anomaly_policy_allows(
        source_scope=source_scope,
        watchlists=validated_watchlists,
    ):
        return None

    anomaly_type = (
        str(
            anomaly.anomaly_type
            or ""
        )
        .strip()
    )

    display_type = (
        anomaly_type
        .replace(
            "_",
            " ",
        )
        if anomaly_type
        else "unexpected change"
    )

    description = (
        anomaly.description.strip()
        if (
            anomaly.description
            and anomaly.description.strip()
        )
        else None
    )

    message = (
        description
        or (
            "Anomaly detection identified an unexpected "
            "semantic change pattern."
        )
    )

    occurred_at = (
        anomaly.detected_at
        or event.detected_at
    )

    return AlertCandidate(
        organization_id=(
            anomaly.organization_id
        ),
        source_type=ALERT_SOURCE_ANOMALY,
        source_id=anomaly.id,
        alert_type=ALERT_TYPE_ANOMALY,
        title=(
            f"Anomaly detected: {display_type}"
        ),
        occurred_at=occurred_at,
        event_id=event.id,
        message=message,
        severity=None,
        affected_entities=(
            source_scope.entity_ids
        ),
    )