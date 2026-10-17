"""Read-only inventory of scheduled sources; does not mutate or pause anything.

Run in the repository's API container:
    python /app/scripts/sde_source_inventory_readonly.py

The heuristic 'https://<entity.domain>/<page_type>' is the same shape as the
historical demo seeder. It is NOT proof of demo ownership.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from urllib.parse import urlsplit

from sqlalchemy import select
from src.core.database import SessionLocal
from src.models import Entity, MonitoredPage


def main() -> None:
    by_org = defaultdict(Counter)
    failure_types = Counter()
    candidates = []
    with SessionLocal() as db:
        rows = db.execute(
            select(MonitoredPage, Entity)
            .join(
                Entity,
                (MonitoredPage.entity_id == Entity.id)
                & (MonitoredPage.organization_id == Entity.organization_id),
            )
        ).all()
        for page, entity in rows:
            bucket = by_org[str(page.organization_id)]
            bucket["total"] += 1
            bucket["active" if page.is_active else "paused"] += 1
            bucket[f"crawl:{page.last_crawl_status or 'not_recorded'}"] += 1
            if page.last_crawl_failure_code:
                failure_types[page.last_crawl_failure_code] += 1
            # This identifies only a URL SHAPE, not the record's true origin.
            seed_shaped = (
                page.url.lower()
                == f"https://{entity.domain.lower()}/{page.page_type.lower()}"
            )
            if seed_shaped:
                bucket["demo_url_shape"] += 1
                candidates.append((str(page.id), urlsplit(page.url).hostname or ""))

    print("Source inventory (read-only, all organizations):")
    for organization, counts in sorted(by_org.items()):
        print(f"  organization={organization} ", dict(sorted(counts.items())))
    print("Failure reason counts:", dict(sorted(failure_types.items())))
    print("Sources with historical demo URL shape:", len(candidates))
    print("Candidate page IDs / hostnames (review ownership before any action):")
    for page_id, hostname in candidates[:20]:
        print(f"  {page_id}  {hostname}")
    if len(candidates) > 20:
        print("  ... truncated")
    print("No database writes were performed. Do not bulk-delete by URL pattern.")


if __name__ == "__main__":
    main()
