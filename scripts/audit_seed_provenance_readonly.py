"""Read-only fingerprint audit for an explicitly selected organization.

This is an operator-only diagnostic, not an API route or an authorization bypass.
It reports evidence consistent with the retired synthetic seeder, without
classifying records as safe to delete or mutating any database rows.

Example:
    python -m scripts.audit_seed_provenance_readonly \
        --organization 08f96bf7-6adb-4b6a-a39e-2379da0de169
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from src.core.database import SessionLocal
from src.models import (
    Alert,
    Anomaly,
    Entity,
    MonitoredPage,
    Organization,
    PageSnapshot,
    Pattern,
    SemanticDriftEvent,
)

# Extracted from the retired scripts/seed_demo_data.py. These constants are
# evidence fingerprints, never cleanup selectors or tenant authorization.
SEED_DOMAINS = frozenset({
    "slack.com", "google.com", "openai.com", "stripe.com", "github.com",
    "microsoft.com", "amazon.com", "notion.com", "aws.amazon.com",
    "cloudflare.com", "atlassian.com", "shopify.com", "netflix.com",
    "spotify.com", "adobe.com", "salesforce.com", "oracle.com", "ibm.com",
    "intel.com", "nvidia.com",
})
SEED_PAGE_TYPES = frozenset({"tos", "privacy", "pricing", "sla", "docs"})
SEED_TRANSITIONS = (
    ("Data retained for 30 days", "Data retained for 90 days", "Data retention period increased 3x"),
    ("Free plan limited to 100 users", "Free plan limited to 50 users", "Free plan user limit reduced"),
    ("No AI training on user data", "AI training on user data enabled", "AI training clause added"),
    ("Annual billing: $120/year", "Annual billing: $168/year", "Price increased 40%"),
    ("Data sharing with partners restricted", "Data sharing with partners enabled", "Data sharing policy changed"),
    ("Support response time: 24 hours", "Support response time: 48 hours", "Support SLA downgraded"),
    ("Security audit: annual", "Security audit: quarterly", "Security audit frequency increased"),
    ("Data retention: 90 days", "Data retention: 180 days", "Data retention period increased"),
    ("Opt-out available", "Opt-out removed", "Opt-out option removed"),
    ("Free plan included", "Free plan removed", "Free plan discontinued"),
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def matches_seed_url(url: str, domain: str, page_type: str) -> bool:
    """A weak, structural signal. It is never sufficient proof on its own."""
    return (
        domain.lower() in SEED_DOMAINS
        and page_type.lower() in SEED_PAGE_TYPES
        and url.lower() == f"https://{domain.lower()}/{page_type.lower()}"
    )


def matches_seed_matrix(domain_page_types: dict[str, set[str]]) -> bool:
    """The historical seeder chose exactly 3 distinct types for each of 20 hosts."""
    return (
        set(domain_page_types) == SEED_DOMAINS
        and all(
            len(types) == 3 and types <= SEED_PAGE_TYPES
            for types in domain_page_types.values()
        )
    )


def audit(organization_id: UUID) -> int:
    with SessionLocal() as db:
        org = db.get(Organization, organization_id)
        if org is None:
            print("Organization not found; no data scanned outside the requested tenant.")
            return 2

        rows = db.execute(
            select(MonitoredPage, Entity)
            .join(
                Entity,
                and_(
                    Entity.id == MonitoredPage.entity_id,
                    Entity.organization_id == MonitoredPage.organization_id,
                ),
            )
            .where(MonitoredPage.organization_id == organization_id)
        ).all()
        source_count = len(rows)
        status_counts = Counter()
        failure_counts = Counter()
        matrix: dict[str, set[str]] = {}
        suspicious_pages = 0
        active_claims = 0
        for page, entity in rows:
            status_counts[page.last_crawl_status or "not_recorded"] += 1
            if page.last_crawl_failure_code:
                failure_counts[page.last_crawl_failure_code] += 1
            if page.crawl_claim_token is not None:
                active_claims += 1
            if matches_seed_url(page.url, entity.domain, page.page_type):
                suspicious_pages += 1
                matrix.setdefault(entity.domain.lower(), set()).add(page.page_type.lower())

        entity_count = db.scalar(
            select(func.count(Entity.id)).where(Entity.organization_id == organization_id)
        ) or 0
        snapshot_total = db.scalar(
            select(func.count(PageSnapshot.id)).where(PageSnapshot.organization_id == organization_id)
        ) or 0
        event_total = db.scalar(
            select(func.count(SemanticDriftEvent.id)).where(
                SemanticDriftEvent.organization_id == organization_id
            )
        ) or 0

        # Exact historical synthetic text fingerprints. The combined content
        # hashes and predictable raw-content paths are much stronger evidence
        # than URL shape alone; other snapshots can coexist in the same org.
        hashes = {_hash(text) for old, new, _ in SEED_TRANSITIONS for text in (old, new)}
        synthetic_snapshots = db.scalar(
            select(func.count(PageSnapshot.id)).where(
                PageSnapshot.organization_id == organization_id,
                PageSnapshot.content_hash.in_(hashes),
                or_(
                    PageSnapshot.raw_content_path.like("data/raw/%!_before.txt", escape="!"),
                    PageSnapshot.raw_content_path.like("data/raw/%!_after.txt", escape="!"),
                ),
            )
        ) or 0

        old_snapshot = aliased(PageSnapshot)
        new_snapshot = aliased(PageSnapshot)
        event_fingerprints = [
            and_(
                old_snapshot.content_hash == _hash(old),
                new_snapshot.content_hash == _hash(new),
                SemanticDriftEvent.summary_text == summary,
            )
            for old, new, summary in SEED_TRANSITIONS
        ]
        synthetic_events = db.scalar(
            select(func.count(SemanticDriftEvent.id))
            .join(
                old_snapshot,
                and_(
                    old_snapshot.id == SemanticDriftEvent.old_snapshot_id,
                    old_snapshot.organization_id == SemanticDriftEvent.organization_id,
                ),
            )
            .join(
                new_snapshot,
                and_(
                    new_snapshot.id == SemanticDriftEvent.new_snapshot_id,
                    new_snapshot.organization_id == SemanticDriftEvent.organization_id,
                ),
            )
            .where(
                SemanticDriftEvent.organization_id == organization_id,
                or_(*event_fingerprints),
                old_snapshot.raw_content_path.like("data/raw/%!_before.txt", escape="!"),
                new_snapshot.raw_content_path.like("data/raw/%!_after.txt", escape="!"),
            )
        ) or 0

        related_counts = {}
        for model, key in ((Pattern, "patterns"), (Anomaly, "anomalies"), (Alert, "alerts")):
            related_counts[key] = db.scalar(
                select(func.count(model.id)).where(model.organization_id == organization_id)
            ) or 0

        matrix_match = (
            source_count == 60
            and entity_count == 20
            and suspicious_pages == 60
            and matches_seed_matrix(matrix)
        )
        print("Seed provenance fingerprint audit — READ ONLY")
        print(f"Organization: {org.id}")
        print(f"Organization slug: {org.slug}")
        print(f"Entities: {entity_count}; monitored sources: {source_count}")
        print(f"Sources matching historical URL shape: {suspicious_pages}")
        print(f"Exact 20-domain / 3-source seeder matrix: {matrix_match}")
        print(f"Last crawl status: {dict(sorted(status_counts.items()))}")
        print(f"Failure codes: {dict(sorted(failure_counts.items()))}")
        print(f"Active crawl claims at read time: {active_claims}")
        print(f"Historical snapshots: {snapshot_total}")
        print(f"Matching synthetic snapshot fingerprints: {synthetic_snapshots}")
        print(f"Historical semantic events: {event_total}")
        print(f"Matching synthetic event-pair fingerprints: {synthetic_events}")
        print(f"Other persisted intelligence counts (not proof of origin): {related_counts}")
        if matrix_match and synthetic_events:
            print("ASSESSMENT: Strong corroborating evidence of historical seed data.")
        elif matrix_match:
            print("ASSESSMENT: Seeder-shaped source matrix, but event provenance is not confirmed.")
        else:
            print("ASSESSMENT: Seeder fingerprint is incomplete; review tenant provenance.")
        print("NO DATABASE WRITES. Do not delete, pause, or classify ownership from this report alone.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", required=True, type=UUID, help="Explicit organization UUID")
    args = parser.parse_args()
    return audit(args.organization)


if __name__ == "__main__":
    raise SystemExit(main())
