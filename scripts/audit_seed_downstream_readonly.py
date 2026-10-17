"""Tenant-scoped, read-only exposure inventory for historical synthetic events.

No remediation actions, ownership inference, raw content, user identity or
integration secrets are produced. Run only with an explicit organization ID.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import aliased

from scripts.audit_seed_provenance_readonly import SEED_TRANSITIONS, _hash
from src.core.database import SessionLocal
from src.models import (
    Alert,
    AlertDelivery,
    Anomaly,
    ApiKey,
    Investigation,
    LineageInvalidationDecisionRecord,
    LineageInvalidationExecutionIntentRecord,
    LineageInvalidationExecutionResultRecord,
    LineageObservationRecord,
    MonitoredPage,
    Organization,
    PageSnapshot,
    Pattern,
    RiskAssessment,
    SemanticDriftEvent,
    TemporalLineageCorrelationRecord,
    User,
    UserSession,
    Watchlist,
    WebhookSubscription,
)


def parse_event_ids(value: object) -> set[UUID]:
    """Use only valid UUID values from historical JSON event-id lists."""
    if not isinstance(value, list):
        return set()
    result = set()
    for item in value:
        if not isinstance(item, str):
            continue
        try:
            result.add(UUID(item))
        except ValueError:
            continue
    return result


def classify_alert_reference(
    event_id: UUID | None,
    source_type: str | None,
    source_id: UUID | None,
    *,
    seed_events: set[UUID],
    related_patterns: set[UUID],
    related_anomalies: set[UUID],
    related_risk_assessments: set[UUID],
) -> bool:
    """Conservative direct/typed references; never infer from alert text."""
    return bool(
        (event_id is not None and event_id in seed_events)
        or (source_type == "pattern" and source_id in related_patterns)
        or (source_type == "anomaly" and source_id in related_anomalies)
        or (source_type == "risk" and source_id in related_risk_assessments)
    )


def _synthetic_event_ids(db, organization_id: UUID) -> set[UUID]:
    old = aliased(PageSnapshot)
    new = aliased(PageSnapshot)
    transitions = [
        and_(
            old.content_hash == _hash(before),
            new.content_hash == _hash(after),
            SemanticDriftEvent.summary_text == summary,
        )
        for before, after, summary in SEED_TRANSITIONS
    ]
    return set(db.scalars(
        select(SemanticDriftEvent.id)
        .join(
            old,
            and_(
                old.id == SemanticDriftEvent.old_snapshot_id,
                old.organization_id == SemanticDriftEvent.organization_id,
            ),
        )
        .join(
            new,
            and_(
                new.id == SemanticDriftEvent.new_snapshot_id,
                new.organization_id == SemanticDriftEvent.organization_id,
            ),
        )
        .where(
            SemanticDriftEvent.organization_id == organization_id,
            or_(*transitions),
            old.raw_content_path.like("data/raw/%!_before.txt", escape="!"),
            new.raw_content_path.like("data/raw/%!_after.txt", escape="!"),
        )
    ).all())


def _synthetic_snapshot_ids(db, organization_id: UUID) -> set[UUID]:
    seed_hashes = {_hash(s) for old, new, _ in SEED_TRANSITIONS for s in (old, new)}
    return set(db.scalars(select(PageSnapshot.id).where(
        PageSnapshot.organization_id == organization_id,
        PageSnapshot.content_hash.in_(seed_hashes),
        or_(
            PageSnapshot.raw_content_path.like("data/raw/%!_before.txt", escape="!"),
            PageSnapshot.raw_content_path.like("data/raw/%!_after.txt", escape="!"),
        ),
    )).all())


def _count(db, model, organization_id: UUID, *conditions) -> int:
    return int(db.scalar(
        select(func.count(model.id)).where(
            model.organization_id == organization_id, *conditions,
        )
    ) or 0)


def audit(organization_id: UUID) -> int:
    with SessionLocal() as db:
        if db.get_bind().dialect.name != "postgresql":
            raise RuntimeError("PostgreSQL is required for the read-only transaction gate")
        # Must be the first SQL statement in the transaction, before any SELECT.
        db.execute(text("SET TRANSACTION READ ONLY"))
        org = db.get(Organization, organization_id)
        if org is None:
            print("Organization not found; no data changed.")
            return 2

        seed_events = _synthetic_event_ids(db, organization_id)
        seed_snapshots = _synthetic_snapshot_ids(db, organization_id)
        total_events = _count(db, SemanticDriftEvent, organization_id)
        total_snapshots = _count(db, PageSnapshot, organization_id)

        users = db.execute(select(User.is_active, User.email_verified).where(
            User.organization_id == organization_id,
        )).all()
        active_sessions = db.scalar(select(func.count(UserSession.id)).join(
            User, User.id == UserSession.user_id,
        ).where(
            User.organization_id == organization_id,
            UserSession.is_revoked.is_(False),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > func.now(),
        )) or 0

        keys = db.execute(select(ApiKey.is_active, ApiKey.last_used_at).where(
            ApiKey.organization_id == organization_id,
        )).all()
        hooks = db.execute(select(WebhookSubscription.is_active).where(
            WebhookSubscription.organization_id == organization_id,
        )).all()

        patterns = db.execute(select(Pattern.id, Pattern.event_ids).where(
            Pattern.organization_id == organization_id,
        )).all()
        related_patterns = {
            row.id for row in patterns if parse_event_ids(row.event_ids) & seed_events
        }
        malformed_pattern_lists = sum(
            1 for row in patterns if not isinstance(row.event_ids, list)
        )
        anomalies = db.execute(select(Anomaly.id, Anomaly.event_id).where(
            Anomaly.organization_id == organization_id,
        )).all()
        related_anomalies = {
            row.id for row in anomalies if row.event_id in seed_events
        }
        related_risk_assessments = set(db.scalars(select(RiskAssessment.id).where(
            RiskAssessment.organization_id == organization_id,
            RiskAssessment.semantic_drift_event_id.in_(seed_events),
        )).all()) if seed_events else set()

        alerts = db.execute(select(
            Alert.id, Alert.event_id, Alert.source_type, Alert.source_id, Alert.status,
        ).where(Alert.organization_id == organization_id)).all()
        implicated_alert_ids = {
            row.id for row in alerts
            if classify_alert_reference(
                row.event_id, row.source_type, row.source_id,
                seed_events=seed_events,
                related_patterns=related_patterns,
                related_anomalies=related_anomalies,
                related_risk_assessments=related_risk_assessments,
            )
        }
        alert_statuses = dict(sorted(Counter(row.status or "unknown" for row in alerts).items()))
        delivery_rows = db.execute(select(
            AlertDelivery.alert_id, AlertDelivery.status, AlertDelivery.delivered_at,
        ).where(AlertDelivery.organization_id == organization_id)).all()
        linked_deliveries = [
            row for row in delivery_rows if row.alert_id in implicated_alert_ids
        ]
        delivery_statuses = dict(sorted(Counter(
            row.status or "unknown" for row in delivery_rows
        ).items()))
        linked_delivery_statuses = dict(sorted(Counter(
            row.status or "unknown" for row in linked_deliveries
        ).items()))

        correlated_seed_snapshots = int(db.scalar(select(func.count()).select_from(
            TemporalLineageCorrelationRecord
        ).join(
            PageSnapshot,
            PageSnapshot.id == TemporalLineageCorrelationRecord.page_snapshot_id,
        ).where(
            PageSnapshot.organization_id == organization_id,
            PageSnapshot.id.in_(seed_snapshots),
        )) or 0) if seed_snapshots else 0

        # These tenant-wide counts cannot establish causal derivation from seed.
        report = {
            "organization": {"id": str(organization_id), "slug": org.slug, "active": bool(org.is_active)},
            "provenance": {
                "synthetic_events": len(seed_events), "all_events": total_events,
                "synthetic_snapshots": len(seed_snapshots), "all_snapshots": total_snapshots,
            },
            "ownership_and_integrations": {
                "users_total": len(users),
                "users_active": sum(bool(row.is_active) for row in users),
                "users_verified": sum(bool(row.email_verified) for row in users),
                "unrevoked_unexpired_sessions": int(active_sessions),
                "api_keys_total": len(keys),
                "api_keys_active": sum(bool(row.is_active) for row in keys),
                "api_keys_used_at_least_once": sum(row.last_used_at is not None for row in keys),
                "webhooks_total": len(hooks),
                "webhooks_active": sum(bool(row.is_active) for row in hooks),
            },
            "intelligence_references": {
                "patterns_total": len(patterns),
                "patterns_referencing_seed_events": len(related_patterns),
                "pattern_event_lists_not_arrays": malformed_pattern_lists,
                "anomalies_total": len(anomalies),
                "anomalies_referencing_seed_events": len(related_anomalies),
                "risk_assessments_referencing_seed_events": len(related_risk_assessments),
                "alerts_total": len(alerts),
                "alerts_with_direct_or_typed_seed_reference": len(implicated_alert_ids),
                "alerts_not_proven_seed_related": len(alerts) - len(implicated_alert_ids),
                "alert_statuses": alert_statuses,
            },
            "external_delivery": {
                "deliveries_total": len(delivery_rows),
                "delivery_statuses": delivery_statuses,
                "deliveries_on_linked_alerts": len(linked_deliveries),
                "statuses_on_linked_alerts": linked_delivery_statuses,
                "linked_deliveries_with_delivered_at": sum(
                    row.delivered_at is not None for row in linked_deliveries
                ),
            },
            "lineage_and_workflows": {
                "correlations_from_matching_seed_snapshots": correlated_seed_snapshots,
                "lineage_observations_org_wide": _count(db, LineageObservationRecord, organization_id),
                "lineage_decisions_org_wide": _count(db, LineageInvalidationDecisionRecord, organization_id),
                "lineage_execution_intents_org_wide": _count(db, LineageInvalidationExecutionIntentRecord, organization_id),
                "lineage_execution_results_org_wide": _count(db, LineageInvalidationExecutionResultRecord, organization_id),
                "watchlists_org_wide": _count(db, Watchlist, organization_id),
                "investigations_org_wide": _count(db, Investigation, organization_id),
                "active_monitored_sources_org_wide": _count(
                    db, MonitoredPage, organization_id, MonitoredPage.is_active.is_(True)
                ),
            },
        }
        print("Seed downstream exposure audit — PostgreSQL READ ONLY transaction")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        print("COUNTS DO NOT ESTABLISH OWNERSHIP OR PERMISSION TO MUTATE.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", type=UUID, required=True)
    args = parser.parse_args()
    return audit(args.organization)


if __name__ == "__main__":
    raise SystemExit(main())
