"""
Shared persistence primitives for monitored-source crawlers.

All crawlers use this module for:

- tenant resolution validation;
- immutable snapshot-file storage;
- entity/page upsert behavior;
- snapshot metadata persistence.

Keeping these invariants in one place prevents individual crawlers from
drifting into different tenancy or storage semantics.
"""

from __future__ import annotations

import os
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.org_utils import (
    require_active_organization,
)
from src.core.paths import Paths
from src.models import (
    Entity,
    MonitoredPage,
    PageSnapshot,
)
from src.services.crawl_scheduling_service import CrawlClaimAuthorityError


@dataclass(
    frozen=True,
    slots=True,
)
class SnapshotFile:
    path: str
    size_bytes: int


def persist_snapshot_content(
    *,
    source_name: str,
    page_type: str,
    content: str,
) -> SnapshotFile:
    """
    Persist normalized snapshot content using an atomic filesystem write.

    Filenames include a UTC timestamp and random suffix so multiple crawls
    of the same source in the same day can never overwrite temporal history.
    """
    if not content:
        raise ValueError(
            "Snapshot content cannot be empty."
        )

    data_directory = (
        Paths.get_data_dir()
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    unique_id = (
        uuid.uuid4().hex
    )

    normalized_source = (
        source_name.strip()
        .lower()
        .replace(" ", "_")
    )

    normalized_page_type = (
        page_type.strip()
        .lower()
        .replace(" ", "_")
    )

    filename = (
        f"{normalized_source}_"
        f"{normalized_page_type}_"
        f"{timestamp}_"
        f"{unique_id}.txt"
    )

    file_path = os.path.join(
        str(
            data_directory
        ),
        filename,
    )

    temporary_path = (
        f"{file_path}.tmp"
    )

    encoded_content = (
        content.encode(
            "utf-8"
        )
    )

    try:
        with open(
            temporary_path,
            "wb",
        ) as snapshot_file:
            snapshot_file.write(
                encoded_content
            )

            snapshot_file.flush()

            os.fsync(
                snapshot_file.fileno()
            )

        os.replace(
            temporary_path,
            file_path,
        )

    except Exception:
        try:
            if os.path.exists(
                temporary_path
            ):
                os.remove(
                    temporary_path
                )
        except OSError:
            pass

        raise

    return SnapshotFile(
        path=file_path,
        size_bytes=len(
            encoded_content
        ),
    )


def _resolve_scheduled_monitored_page(
    *,
    db: Session,
    organization_id: uuid.UUID,
    monitored_page_id: uuid.UUID,
    url: str,
    page_type: str,
    claim_token: uuid.UUID | None,
) -> MonitoredPage:
    monitored_page = (
        db.query(
            MonitoredPage
        )
        .filter(
            MonitoredPage.id
            == monitored_page_id,
            MonitoredPage.organization_id
            == organization_id,
        )
        .with_for_update()
        .one_or_none()
    )

    if monitored_page is None:
        raise ValueError(
            "Scheduled monitored page does not exist "
            "for the organization."
        )

    if monitored_page.url != url:
        raise ValueError(
            "Scheduled monitored page URL does not "
            "match the crawler URL."
        )

    if monitored_page.page_type != page_type:
        raise ValueError(
            "Scheduled monitored page type does not "
            "match the crawler page type."
        )

    # Serialize persistence against claim reclaim/finalization. A scheduler
    # worker cannot commit evidence if its lease has expired or a newer
    # worker now owns the monitored page. Manual writes remain allowed only
    # while no scheduler claim is active.
    if claim_token is None:
        if monitored_page.crawl_claim_token is not None:
            raise CrawlClaimAuthorityError(
                "Monitored page has an active scheduled crawl claim."
            )
    else:
        effective_now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()
        if (
            monitored_page.crawl_claim_token != claim_token
            or monitored_page.crawl_claim_expires_at is None
            or monitored_page.crawl_claim_expires_at <= effective_now
        ):
            raise CrawlClaimAuthorityError(
                "Scheduled crawl claim no longer authorizes snapshot persistence."
            )

    return monitored_page


def _resolve_or_create_monitored_page(
    *,
    db: Session,
    organization_id: uuid.UUID,
    entity_name: str,
    entity_domain: str,
    entity_category: str | None,
    url: str,
    page_type: str,
) -> MonitoredPage:
    entity = (
        db.query(
            Entity
        )
        .filter(
            Entity.organization_id
            == organization_id,
            Entity.domain
            == entity_domain,
        )
        .first()
    )

    if entity is None:
        entity = Entity(
            organization_id=organization_id,
            domain=entity_domain,
            name=entity_name,
            category=entity_category,
        )

        db.add(
            entity
        )

        db.flush()

    else:
        changed = False

        if (
            not entity.name
            and entity_name
        ):
            entity.name = (
                entity_name
            )
            changed = True

        if (
            not entity.category
            and entity_category
        ):
            entity.category = (
                entity_category
            )
            changed = True

        if changed:
            db.flush()

    monitored_page = (
        db.query(
            MonitoredPage
        )
        .filter(
            MonitoredPage.organization_id
            == organization_id,
            MonitoredPage.entity_id
            == entity.id,
            MonitoredPage.url
            == url,
        )
        .with_for_update()
        .first()
    )

    if monitored_page is None:
        monitored_page = MonitoredPage(
            organization_id=organization_id,
            entity_id=entity.id,
            url=url,
            page_type=page_type,
        )

        db.add(
            monitored_page
        )

        db.flush()

    elif monitored_page.crawl_claim_token is not None:
        # The manual path may resolve an existing scheduled page without
        # knowing its monitored_page_id. It must not bypass a live claim.
        raise CrawlClaimAuthorityError(
            "Monitored page has an active scheduled crawl claim."
        )

    return monitored_page


def create_snapshot_record(
    *,
    db: Session,
    organization_id: uuid.UUID,
    entity_name: str,
    entity_domain: str,
    entity_category: str | None,
    url: str,
    page_type: str,
    content_hash: str,
    snapshot_file: SnapshotFile,
    monitored_page_id: uuid.UUID | None = None,
    claim_token: uuid.UUID | None = None,
) -> PageSnapshot:
    """
    Persist entity, monitored page and snapshot metadata for one tenant.

    The organization is validated before any entity/page mutation occurs.
    Every lookup is organization-scoped so another tenant's entity or page
    can never be reused accidentally.
    """
    if claim_token is not None and monitored_page_id is None:
        raise CrawlClaimAuthorityError(
            "Scheduled crawl claim requires an exact monitored page."
        )

    require_active_organization(
        db,
        organization_id,
    )

    normalized_domain = (
        entity_domain
        .strip()
        .lower()
    )

    normalized_name = (
        entity_name.strip()
    )

    normalized_category = (
        entity_category.strip()
        if entity_category
        and entity_category.strip()
        else None
    )

    normalized_url = (
        url.strip()
    )

    if not normalized_domain:
        raise ValueError(
            "Entity domain cannot be empty."
        )

    if not normalized_name:
        raise ValueError(
            "Entity name cannot be empty."
        )

    if not normalized_url:
        raise ValueError(
            "Monitored page URL cannot be empty."
        )

    if monitored_page_id is not None:
        monitored_page = (
            _resolve_scheduled_monitored_page(
                db=db,
                organization_id=organization_id,
                monitored_page_id=monitored_page_id,
                url=normalized_url,
                page_type=page_type,
                claim_token=claim_token,
            )
        )

    else:
        monitored_page = (
            _resolve_or_create_monitored_page(
                db=db,
                organization_id=organization_id,
                entity_name=normalized_name,
                entity_domain=normalized_domain,
                entity_category=normalized_category,
                url=normalized_url,
                page_type=page_type,
            )
        )

    snapshot = PageSnapshot(
        organization_id=organization_id,
        monitored_page_id=(
            monitored_page.id
        ),
        content_hash=content_hash,
        raw_content=None,
        content_size=(
            snapshot_file.size_bytes
        ),
        raw_content_path=(
            snapshot_file.path
        ),
    )

    db.add(
        snapshot
    )

    db.commit()

    db.refresh(
        snapshot
    )

    return snapshot