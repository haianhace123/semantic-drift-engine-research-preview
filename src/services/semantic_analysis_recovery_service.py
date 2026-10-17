"""Bounded recovery for persisted failed semantic drift analyses.

PostgreSQL remains the only lifecycle authority. A session-scoped advisory
lock serializes external provider calls per event without keeping a long
row-level transaction open. Failed events never become downstream-ready
until the semantic result has been validated and committed.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.core.database import SessionLocal, engine
from src.detector.change_detector import (
    _calculate_similarity,
    _process_semantic_analysis,
    _semantic_event_fields,
    utc_now,
)
from src.diff_engine.deepseek_diff import (
    DeepSeekPermanentError,
    DeepSeekSemanticDiffError,
    DeepSeekTransientError,
)
from src.models import Entity, MonitoredPage, Organization, PageSnapshot, SemanticDriftEvent
from src.models.risk_assessment import RiskAssessment
from src.services.graph_service import persist_event_relationships
from src.services.relationship_evidence_service import extract_relationship_evidence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecoveryResult:
    event_id: str
    outcome: str
    attempts: int | None = None


def _lock_key(event_id: uuid.UUID) -> int:
    """Stable signed 64-bit key for a PostgreSQL session advisory lock."""
    value = hashlib.blake2b(event_id.bytes, digest_size=8, person=b"sde-sem").digest()
    return int.from_bytes(value, byteorder="big", signed=True)


def _aware(timestamp: datetime) -> datetime:
    return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)


# Only pending events created by the snapshot gap registrar may be admitted
# to this recovery worker. Other pending lifecycle states retain their owner.
GAP_RECOVERY_SOURCE = "snapshot_gap"
MAX_GAP_SCAN = 10000


def _registered_gap(event: SemanticDriftEvent) -> bool:
    delta = event.semantic_delta_json or {}
    if not isinstance(delta, dict):
        return False
    marker = delta.get("analysis_recovery")
    return isinstance(marker, dict) and marker.get("source") == GAP_RECOVERY_SOURCE


@dataclass(frozen=True)
class MissingSnapshotPair:
    organization_id: uuid.UUID
    monitored_page_id: uuid.UUID
    old_snapshot_id: uuid.UUID
    new_snapshot_id: uuid.UUID


def missing_snapshot_pair_candidates(
    *, limit: int = 5, scan_limit: int = 2000,
    organization_id: uuid.UUID | None = None,
) -> list[MissingSnapshotPair]:
    """Read-only bounded scan of recent observations, including older pairs.

    The lateral predecessor is resolved against ALL history for the page;
    only the *new* observation is restricted by scan_limit. Older gaps beyond
    this window require a scoped operator reconciliation with a larger window.
    No provider calls or filesystem reads occur during discovery.
    """
    if limit < 1 or scan_limit < limit or scan_limit > MAX_GAP_SCAN:
        raise ValueError("Invalid semantic gap scan bounds.")
    statement = text("""
        WITH scan AS (
            SELECT n.id, n.organization_id, n.monitored_page_id,
                   n.content_hash, n.crawled_at
            FROM page_snapshots n
            JOIN organizations o ON o.id = n.organization_id AND o.is_active IS TRUE
            WHERE n.crawled_at IS NOT NULL
              AND (CAST(:organization_id AS uuid) IS NULL OR n.organization_id = CAST(:organization_id AS uuid))
            ORDER BY n.crawled_at DESC, n.id DESC
            LIMIT :scan_limit
        )
        SELECT n.organization_id, n.monitored_page_id,
               prev.id AS old_snapshot_id, n.id AS new_snapshot_id
        FROM scan n
        JOIN LATERAL (
            SELECT p.id, p.content_hash
            FROM page_snapshots p
            WHERE p.organization_id = n.organization_id
              AND p.monitored_page_id = n.monitored_page_id
              AND (p.crawled_at, p.id) < (n.crawled_at, n.id)
            ORDER BY p.crawled_at DESC, p.id DESC
            LIMIT 1
        ) prev ON TRUE
        WHERE prev.content_hash <> n.content_hash
          AND NOT EXISTS (
              SELECT 1 FROM semantic_drift_events e
              WHERE e.old_snapshot_id = prev.id AND e.new_snapshot_id = n.id
          )
        ORDER BY n.crawled_at ASC, n.id ASC
        LIMIT :limit
    """)
    with SessionLocal() as db:
        rows = db.execute(statement, {
            "organization_id": str(organization_id) if organization_id else None,
            "scan_limit": scan_limit, "limit": limit,
        }).mappings().all()
    return [MissingSnapshotPair(**dict(row)) for row in rows]


def register_missing_snapshot_pair(pair: MissingSnapshotPair) -> uuid.UUID | None:
    """Atomically reserve a changed adjacent pair as pending semantic work.

    Recheck its tenancy and adjacency under the same monitored-page row lock
    used by snapshot writers. The database unique constraint is authoritative
    when multiple recovery workers register the same pair concurrently.
    """
    with SessionLocal() as db:
        page = db.query(MonitoredPage).filter(
            MonitoredPage.id == pair.monitored_page_id,
            MonitoredPage.organization_id == pair.organization_id,
        ).with_for_update().one_or_none()
        org = db.query(Organization).filter(
            Organization.id == pair.organization_id,
            Organization.is_active.is_(True),
        ).one_or_none()
        if page is None or org is None:
            return None
        rows = db.query(PageSnapshot).filter(
            PageSnapshot.id.in_([pair.old_snapshot_id, pair.new_snapshot_id]),
            PageSnapshot.organization_id == pair.organization_id,
            PageSnapshot.monitored_page_id == pair.monitored_page_id,
        ).all()
        by_id = {r.id: r for r in rows}
        old = by_id.get(pair.old_snapshot_id)
        new = by_id.get(pair.new_snapshot_id)
        if (old is None or new is None or old.id == new.id
            or old.crawled_at is None or new.crawled_at is None
            or (old.crawled_at, old.id) >= (new.crawled_at, new.id)
            or old.content_hash == new.content_hash):
            return None
        intervening = db.query(PageSnapshot.id).filter(
            PageSnapshot.organization_id == pair.organization_id,
            PageSnapshot.monitored_page_id == pair.monitored_page_id,
            tuple_(PageSnapshot.crawled_at, PageSnapshot.id) > (old.crawled_at, old.id),
            tuple_(PageSnapshot.crawled_at, PageSnapshot.id) < (new.crawled_at, new.id),
        ).first()
        if intervening:
            return None
        event_id = uuid.uuid4()
        statement = pg_insert(SemanticDriftEvent).values(
            id=event_id,
            organization_id=pair.organization_id,
            monitored_page_id=pair.monitored_page_id,
            old_snapshot_id=old.id, new_snapshot_id=new.id,
            semantic_delta_json={
                "change_detected": True,
                "old_hash": old.content_hash, "new_hash": new.content_hash,
                "semantic_analysis": {"analysis_status": "pending"},
                "analysis_recovery": {"source": GAP_RECOVERY_SOURCE},
            },
            summary_text="Semantic analysis pending; evidence is not complete.",
            analysis_status="pending", analysis_attempts=0,
            risk_level="unknown", confidence=0.0, impact_score=0,
        ).on_conflict_do_nothing(
            constraint="uq_semantic_drift_event_snapshot_pair"
        ).returning(SemanticDriftEvent.id)
        inserted = db.execute(statement).scalar_one_or_none()
        db.commit()
        return inserted


def reconcile_missing_snapshot_pairs(
    *, limit: int = 5, scan_limit: int = 2000,
    organization_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """Reserve at most `limit` missing pairs; recovery runs separately."""
    registered = []
    for pair in missing_snapshot_pair_candidates(
        limit=limit, scan_limit=scan_limit, organization_id=organization_id,
    ):
        event_id = register_missing_snapshot_pair(pair)
        if event_id is not None:
            registered.append(event_id)
    return registered


def _retryable(event: SemanticDriftEvent) -> bool:
    delta = event.semantic_delta_json or {}
    failure = delta.get("analysis_failure") if isinstance(delta, dict) else None
    if isinstance(failure, dict) and failure.get("retryable") is False:
        return False
    # Legacy failures predate typed failure metadata. These are deterministic.
    message = (event.analysis_error or "").lower()
    return not any(marker in message for marker in (
        "no partial analysis", "deepseek_api_key not set",
        "cannot perform semantic comparison with empty text",
        "must be greater than 0",
    ))


def _eligible(
    event: SemanticDriftEvent,
    *,
    now: datetime,
    max_attempts: int,
    base_retry_seconds: int,
) -> bool:
    is_gap = event.analysis_status == "pending" and _registered_gap(event)
    if not (event.analysis_status == "failed" or is_gap) or not _retryable(event):
        return False
    attempts = max(0, int(event.analysis_attempts or 0))
    if is_gap and attempts == 0:
        return True
    if attempts >= max_attempts:
        return False
    last_attempt = event.analyzed_at or event.detected_at
    if last_attempt is None:
        return True
    delay = base_retry_seconds * (2 ** max(0, attempts - 1))
    return now >= _aware(last_attempt) + timedelta(seconds=delay)


def recovery_candidates(
    *,
    limit: int = 10,
    organization_id: uuid.UUID | None = None,
    max_attempts: int = 3,
    base_retry_seconds: int = 60,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    """Select due failed analyses and registered pending gaps, omitting permanent failures."""
    if limit < 1 or max_attempts < 1 or base_retry_seconds < 0:
        raise ValueError("Invalid semantic recovery limits.")
    now = now or utc_now()
    db = SessionLocal()
    try:
        retryable_json = SemanticDriftEvent.semantic_delta_json[
            "analysis_failure"
        ]["retryable"].as_string()
        query = db.query(SemanticDriftEvent).filter(
            or_(
                SemanticDriftEvent.analysis_status == "failed",
                and_(
                    SemanticDriftEvent.analysis_status == "pending",
                    SemanticDriftEvent.semantic_delta_json[
                        "analysis_recovery"
                    ]["source"].as_string() == GAP_RECOVERY_SOURCE,
                ),
            ),
            SemanticDriftEvent.analysis_attempts < max_attempts,
            func.coalesce(retryable_json, "true") != "false",
        )
        if organization_id is not None:
            query = query.filter(SemanticDriftEvent.organization_id == organization_id)
        query = query.order_by(
            SemanticDriftEvent.detected_at.asc(), SemanticDriftEvent.id.asc()
        )
        result: list[uuid.UUID] = []
        # Stream, rather than cap at N stale/permanent rows that would starve
        # newer eligible events. The SQL query already filters typed failures.
        for event in query.yield_per(100):
            if _eligible(
                event, now=now, max_attempts=max_attempts,
                base_retry_seconds=base_retry_seconds,
            ):
                result.append(event.id)
                if len(result) >= limit:
                    break
        return result
    finally:
        db.close()


def _validate_ownership(db: Session, event: SemanticDriftEvent):
    page = db.query(MonitoredPage).filter(
        MonitoredPage.id == event.monitored_page_id,
        MonitoredPage.organization_id == event.organization_id,
    ).one_or_none()
    old = db.query(PageSnapshot).filter(
        PageSnapshot.id == event.old_snapshot_id,
        PageSnapshot.organization_id == event.organization_id,
        PageSnapshot.monitored_page_id == event.monitored_page_id,
    ).one_or_none()
    new = db.query(PageSnapshot).filter(
        PageSnapshot.id == event.new_snapshot_id,
        PageSnapshot.organization_id == event.organization_id,
        PageSnapshot.monitored_page_id == event.monitored_page_id,
    ).one_or_none()
    if page is None or old is None or new is None or old.id == new.id:
        raise RuntimeError("Semantic recovery snapshot ownership invariant failed.")
    return page, old, new


def _read_verified(snapshot: PageSnapshot) -> str:
    path = snapshot.raw_content_path
    if not path or not os.path.isfile(path):
        raise DeepSeekPermanentError("Snapshot file is missing; restore source data.")
    with open(path, encoding="utf-8") as file:
        content = file.read()
    if not content.strip():
        raise DeepSeekPermanentError("Snapshot content is empty; restore source data.")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if digest != snapshot.content_hash:
        raise DeepSeekPermanentError("Snapshot content hash mismatch; recovery blocked.")
    return content


def _record_failure(event: SemanticDriftEvent, exc: Exception) -> None:
    delta = dict(event.semantic_delta_json or {})
    delta["semantic_analysis"] = {"analysis_status": "failed"}
    delta["analysis_failure"] = {
        "retryable": not isinstance(exc, DeepSeekPermanentError),
        "code": (
            "permanent" if isinstance(exc, DeepSeekPermanentError)
            else "transient_or_invalid_response"
        ),
    }
    event.semantic_delta_json = delta
    event.analysis_status = "failed"
    event.analysis_error = (str(exc).strip() or type(exc).__name__)[:500]
    event.confidence = 0.0
    event.impact_score = 0
    event.changes_count = 0
    event.summary_text = "Semantic analysis failed; evidence is not complete."
    event.analyzed_at = utc_now()


def _enrich_relationships(db: Session, event: SemanticDriftEvent, page: MonitoredPage) -> None:
    try:
        entities = db.query(Entity).filter(
            Entity.organization_id == event.organization_id
        ).all()
        evidence = extract_relationship_evidence(
            semantic_delta_json=event.semantic_delta_json,
            summary_text=event.summary_text,
            source_entity_id=page.entity_id,
            entities=entities,
        )
        event.entities_affected = list(evidence.get("entities_affected") or [])
        with db.begin_nested():
            persist_event_relationships(
                db=db, event=event, source_entity_id=page.entity_id,
                target_entity_ids=event.entities_affected,
            )
    except Exception:
        logger.exception("Relationship enrichment failed on semantic recovery %s", event.id)
        db.rollback()  # Never commit partial graph writes or an invalid transaction.
        raise


def recover_event(
    event_id: uuid.UUID,
    *,
    organization_id: uuid.UUID | None = None,
    max_attempts: int = 3,
    base_retry_seconds: int = 60,
    now: datetime | None = None,
    requalify: bool = False,
) -> RecoveryResult:
    """Recover one event in place. Provider calls never occur without a lock."""
    if requalify and organization_id is None:
        raise ValueError("Operator requalification requires a specific organization.")
    if max_attempts < 1 or base_retry_seconds < 0:
        raise ValueError("Invalid semantic recovery limits.")
    now = now or utc_now()
    key = _lock_key(event_id)
    with engine.connect() as connection:
        claimed = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
        ).scalar_one()
        connection.commit()
        if not claimed:
            return RecoveryResult(str(event_id), "locked")
        try:
            with Session(bind=connection, autoflush=False, expire_on_commit=False) as db:
                event = db.query(SemanticDriftEvent).filter(
                    SemanticDriftEvent.id == event_id
                ).one_or_none()
                if event is None or (
                    organization_id is not None and event.organization_id != organization_id
                ):
                    return RecoveryResult(str(event_id), "not_found")
                if not (
                    event.analysis_status == "failed" or (
                        event.analysis_status == "pending" and _registered_gap(event)
                    )
                ) or (
                    not requalify and not _eligible(
                        event, now=now, max_attempts=max_attempts,
                        base_retry_seconds=base_retry_seconds,
                    )
                ):
                    return RecoveryResult(str(event_id), "not_due", event.analysis_attempts)
                # Do not rewrite events with historical downstream decisions.
                if (
                    event.risk_scored_at is not None
                    or event.risk_scoring_status in {"processing", "completed"}
                    or event.temporal_invalidation_status in {"processing", "completed"}
                    or db.query(RiskAssessment.id).filter(
                        RiskAssessment.semantic_drift_event_id == event_id
                    ).first()
                ):
                    return RecoveryResult(str(event_id), "downstream_conflict", event.analysis_attempts)
                try:
                    page, old, new = _validate_ownership(db, event)
                except RuntimeError:
                    logger.exception("Recovery ownership check failed for %s", event_id)
                    return RecoveryResult(str(event_id), "integrity_error", event.analysis_attempts)

                # Reserve attempt DURABLY before provider I/O. A process crash
                # consumes an attempt but cannot forge a completed analysis.
                event.analysis_attempts = max(0, event.analysis_attempts or 0) + 1
                event.analyzed_at = utc_now()
                if requalify:
                    audit = dict(event.semantic_delta_json or {})
                    audit["analysis_recovery"] = {
                        "operator_requalified_at": utc_now().isoformat(),
                        "attempts_after_claim": event.analysis_attempts,
                    }
                    event.semantic_delta_json = audit
                db.commit()
                try:
                    old_text = _read_verified(old)
                    new_text = _read_verified(new)
                    delta, summary = _process_semantic_analysis(
                        page_id=str(page.id), old_snapshot=old,
                        new_snapshot=new, old_text=old_text, new_text=new_text,
                    )
                    fields = _semantic_event_fields(delta, summary)
                except Exception as exc:
                    if isinstance(exc, (OSError, UnicodeError)):
                        exc = DeepSeekPermanentError("Snapshot content is unreadable.")
                    elif not isinstance(exc, DeepSeekSemanticDiffError):
                        logger.exception("Unexpected semantic recovery error for %s", event_id)
                        exc = DeepSeekTransientError("Semantic recovery failed unexpectedly.")
                    _record_failure(event, exc)
                    db.commit()
                    return RecoveryResult(str(event_id), "failed", event.analysis_attempts)

                # Update the existing row, never INSERT a replacement event.
                # Preserve the durable provenance for a gap-reserved event.
                if requalify or _registered_gap(event):
                    fields["delta_json"]["analysis_recovery"] = (
                        event.semantic_delta_json.get("analysis_recovery")
                    )
                event.semantic_delta_json = fields["delta_json"]
                event.summary_text = fields["summary"]
                for attr in (
                    "risk_level", "confidence", "impact_score", "category",
                    "change_type", "changes_count", "chunk_count", "llm_tokens_used",
                    "analysis_status", "analysis_error",
                ):
                    setattr(event, attr, fields[attr])
                event.analyzed_at = utc_now()
                # A prior not-ready delay must not hold the newly-ready event
                # out of the existing vector/risk worker's selection query.
                event.risk_retry_after = None
                try:
                    _enrich_relationships(db, event, page)
                except Exception:
                    # The savepoint/transaction may be invalid. Record an
                    # explicit retryable failure after a full rollback.
                    db.rollback()
                    event = db.get(SemanticDriftEvent, event_id)
                    _record_failure(
                        event, DeepSeekTransientError("Relationship enrichment failed."),
                    )
                    db.commit()
                    return RecoveryResult(str(event_id), "enrichment_error", event.analysis_attempts)
                db.commit()
                return RecoveryResult(str(event_id), "completed", event.analysis_attempts)
        finally:
            # Session locks survive COMMIT, and are automatically released if
            # the process/connection dies. Explicitly release before pooling.
            connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": key}
            ).scalar_one()
            connection.commit()
