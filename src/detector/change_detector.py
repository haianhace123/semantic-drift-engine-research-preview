"""
Production semantic drift detector.

Responsibilities
----------------
1. Select the two newest usable snapshots for a monitored page.
2. Detect deterministic content changes using SHA-256 hashes.
3. Preserve changed clauses for semantic analysis, regardless of edit size.
4. Run semantic analysis without treating text similarity as risk evidence.
5. Persist a production-grade SemanticDriftEvent.
6. Prevent duplicate events for the same snapshot pair.
7. Persist analysis failures explicitly instead of pretending they succeeded.
8. Send notifications only for successfully analysed high-risk events.

Design principles
-----------------
- Snapshot persistence is owned by the crawler layer.
- Historical snapshots are never mutated.
- Database writes are transactional.
- All application timestamps are UTC.
- A failed LLM analysis is represented as `analysis_status="failed"`.
- An invalid/malformed LLM response is never converted into a successful event.
- The detector is safe to run repeatedly for the same page.
"""

from __future__ import annotations

import argparse
import difflib
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from sqlalchemy.exc import IntegrityError


# ---------------------------------------------------------------------------
# Project path support
# ---------------------------------------------------------------------------

# Supports:
#
#     python -m src.detector.change_detector
#
# and direct execution from the project root.
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )
)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


from src.core.database import SessionLocal
from src.diff_engine import deepseek_diff as semantic_response_contract
from src.diff_engine.deepseek_diff import (
    DeepSeekSemanticDiffEngine,
    DeepSeekSemanticDiffError,
    DeepSeekPermanentError,
)
from src.models import (
    Entity,
    MonitoredPage,
    PageSnapshot,
    SemanticDriftEvent,
)
from src.utils.text_chunker import TextChunker
from src.services.relationship_evidence_service import (
    extract_relationship_evidence,
)
from src.services.graph_service import (
    persist_event_relationships,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANALYSIS_STATUS_COMPLETED = "completed"
ANALYSIS_STATUS_FAILED = "failed"

RISK_HIGH = "high"
RISK_MEDIUM = "medium"
RISK_LOW = "low"

DEFAULT_CONFIDENCE = 0.85

MAX_SNAPSHOT_CANDIDATES = 100


# ---------------------------------------------------------------------------
# Time utilities
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    """
    Return the current timezone-aware UTC datetime.
    """
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _safe_float(
    value: Any,
    default: float = DEFAULT_CONFIDENCE,
) -> float:
    """
    Convert a value to float safely and clamp it to [0, 1].
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default

    return max(0.0, min(1.0, number))


def _normalize_risk(
    value: Any,
    default: str = RISK_MEDIUM,
) -> str:
    """
    Normalize a risk level to the production-supported values.
    """
    risk = str(value or "").strip().lower()

    if risk in {RISK_HIGH, RISK_MEDIUM, RISK_LOW}:
        return risk

    return default


def _calculate_impact_score(
    changes: list[dict[str, Any]],
    overall_risk: str,
) -> int:
    """
    Calculate a deterministic 0-100 impact score.

    The score is deliberately conservative.

    High-risk changes dominate the score, while multiple medium/low
    changes increase the total without allowing the value to exceed 100.
    """
    if not changes:
        return 0

    score = 0

    for change in changes:
        risk = _normalize_risk(
            change.get("risk_level"),
            default=RISK_LOW,
        )

        if risk == RISK_HIGH:
            score += 45
        elif risk == RISK_MEDIUM:
            score += 25
        else:
            score += 10

    overall_risk = _normalize_risk(
        overall_risk,
        default=RISK_MEDIUM,
    )

    if overall_risk == RISK_HIGH:
        score += 20
    elif overall_risk == RISK_MEDIUM:
        score += 10

    return min(score, 100)


def _infer_change_type(
    changes: list[dict[str, Any]],
) -> str:
    """
    Infer a stable change type from semantic changes.

    Supported values:
        addition
        modification
        removal
        restructuring
    """
    if not changes:
        return "modification"

    additions = 0
    removals = 0
    modifications = 0

    for change in changes:
        old_meaning = str(
            change.get("old_meaning", "")
        ).strip()

        new_meaning = str(
            change.get("new_meaning", "")
        ).strip()

        if not old_meaning and new_meaning:
            additions += 1

        elif old_meaning and not new_meaning:
            removals += 1

        else:
            modifications += 1

    if additions and not removals and not modifications:
        return "addition"

    if removals and not additions and not modifications:
        return "removal"

    if modifications:
        return "modification"

    return "restructuring"


def _infer_category(
    changes: list[dict[str, Any]],
) -> Optional[str]:
    """
    Infer the dominant semantic category.

    Returns None when no semantic category can be determined.
    """
    if not changes:
        return None

    priority = {
        "legal": 5,
        "security": 5,
        "pricing": 4,
        "policy": 3,
        "feature": 2,
        "other": 1,
    }

    categories: list[str] = []

    for change in changes:
        category = str(
            change.get("category", "")
        ).strip().lower()

        if category:
            categories.append(category)

    if not categories:
        return None

    return max(
        categories,
        key=lambda item: priority.get(item, 0),
    )


def _estimate_confidence(
    semantic_result: dict[str, Any],
) -> float:
    """
    Estimate confidence from the structure of a validated semantic result.

    The DeepSeek engine currently returns validated semantic data but does
    not expose a model confidence field. We therefore use a conservative
    deterministic confidence estimate rather than inventing a model score.
    """
    changes = semantic_result.get("changes")

    if not isinstance(changes, list):
        return DEFAULT_CONFIDENCE

    if not changes:
        return 0.90

    required_fields = (
        "category",
        "old_meaning",
        "new_meaning",
        "impact",
        "risk_level",
    )

    complete = 0

    for change in changes:
        if not isinstance(change, dict):
            continue

        if all(
            isinstance(change.get(field), str)
            for field in required_fields
        ):
            complete += 1

    if complete == len(changes):
        return 0.90

    return 0.80


# ---------------------------------------------------------------------------
# Snapshot change detection
# ---------------------------------------------------------------------------

def detect_changes_for_page(
    page_id: str,
) -> Tuple[
    bool,
    Optional[PageSnapshot],
    Optional[PageSnapshot],
]:
    """
    Compare the two newest usable snapshots of a monitored page.

    Snapshot records whose physical files no longer exist are ignored.
    Historical DB rows are not deleted or modified.
    """
    db = SessionLocal()

    try:
        snapshot_candidates = (
            db.query(PageSnapshot)
            .filter(
                PageSnapshot.monitored_page_id == page_id
            )
            .order_by(
                PageSnapshot.crawled_at.desc(),
                PageSnapshot.id.desc(),
            )
            .limit(MAX_SNAPSHOT_CANDIDATES)
            .all()
        )

        usable_snapshots: list[PageSnapshot] = []

        for snapshot in snapshot_candidates:
            path = snapshot.raw_content_path

            if not path:
                logger.warning(
                    "Ignoring snapshot %s for page %s: "
                    "raw_content_path is empty.",
                    snapshot.id,
                    page_id,
                )
                continue

            resolved_path = os.path.abspath(path)

            if not os.path.isfile(resolved_path):
                logger.warning(
                    "Ignoring snapshot %s for page %s: "
                    "snapshot file does not exist: %s",
                    snapshot.id,
                    page_id,
                    resolved_path,
                )
                continue

            usable_snapshots.append(snapshot)

            if len(usable_snapshots) == 2:
                break

        if len(usable_snapshots) < 2:
            logger.info(
                "Insufficient usable snapshots for page %s. "
                "Found %d, minimum required is 2.",
                page_id,
                len(usable_snapshots),
            )
            return False, None, None

        new_snapshot = usable_snapshots[0]
        old_snapshot = usable_snapshots[1]

        old_hash = old_snapshot.content_hash
        new_hash = new_snapshot.content_hash

        if old_hash == new_hash:
            logger.info(
                "No content change detected for page %s.",
                page_id,
            )
            return False, None, None

        logger.info(
            "Content change detected for page %s: "
            "%s... -> %s...",
            page_id,
            str(old_hash)[:16],
            str(new_hash)[:16],
        )

        return True, old_snapshot, new_snapshot

    except Exception:
        logger.exception(
            "Failed to detect changes for page %s.",
            page_id,
        )
        raise

    finally:
        db.close()


# ---------------------------------------------------------------------------
# Text similarity
# ---------------------------------------------------------------------------

def _calculate_similarity(
    old_text: str,
    new_text: str,
) -> float:
    """
    Calculate deterministic text similarity in [0, 1].
    """
    return difflib.SequenceMatcher(
        None,
        old_text,
        new_text,
    ).ratio()


# ---------------------------------------------------------------------------
# Duplicate protection
# ---------------------------------------------------------------------------

def _find_existing_event(
    old_snapshot_id: str,
    new_snapshot_id: str,
) -> Optional[SemanticDriftEvent]:
    """
    Return an existing event for the same snapshot pair.

    This mirrors the database unique constraint:
        (old_snapshot_id, new_snapshot_id)

    Application-level lookup provides a clean result before attempting
    another INSERT.
    """
    db = SessionLocal()

    try:
        return (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.old_snapshot_id
                == old_snapshot_id,
                SemanticDriftEvent.new_snapshot_id
                == new_snapshot_id,
            )
            .first()
        )

    finally:
        db.close()


# ---------------------------------------------------------------------------
# Semantic analysis
# ---------------------------------------------------------------------------

def _process_semantic_analysis(
    page_id: str,
    old_snapshot: PageSnapshot,
    new_snapshot: PageSnapshot,
    old_text: str,
    new_text: str,
) -> Tuple[
    dict[str, Any],
    str,
]:
    """
    Perform semantic analysis using the DeepSeek engine.

    Changed regions include surrounding text so short edits retain their
    clause-level meaning. No similarity threshold can suppress a change.
    """
    changed_old, changed_new = (
        TextChunker.find_changed_regions_with_context(
            old_text,
            new_text,
            context_chars=240,
        )
    )

    if not changed_old:
        if old_text != new_text:
            raise DeepSeekSemanticDiffError(
                "Changed-region extraction returned no evidence for "
                "nonidentical snapshots."
            )

        logger.info(
            "Snapshot texts are identical for page %s.",
            page_id,
        )

        delta_json = {
            "change_detected": True,
            "old_hash": old_snapshot.content_hash,
            "new_hash": new_snapshot.content_hash,
            "similarity": _calculate_similarity(
                old_text,
                new_text,
            ),
            "chunks_analyzed": 0,
            "semantic_analysis": {
                "analysis_status": "success",
                "changes": [],
                "summary": (
                    "No significant semantic changes detected."
                ),
                "overall_risk": RISK_LOW,
            },
            "detected_at": utc_now().isoformat(),
        }

        return (
            delta_json,
            "No significant semantic changes detected.",
        )

    # Pair each changed region explicitly. Without these boundaries,
    # distant edits can be read as a single fabricated sentence.
    old_changed_text = "\n\n".join(
        f"[Changed region {index}]\n{text}"
        for index, text in enumerate(changed_old, start=1)
    )
    new_changed_text = "\n\n".join(
        f"[Changed region {index}]\n{text}"
        for index, text in enumerate(changed_new, start=1)
    )

    logger.info(
        "Sending %d changed chunks to DeepSeek for page %s.",
        len(changed_old),
        page_id,
    )

    engine = DeepSeekSemanticDiffEngine()

    diff_result = engine.compare(
        old_changed_text,
        new_changed_text,
    )

    semantic_result = (
        dict(diff_result)
        if isinstance(diff_result, dict)
        else {}
    )
    # Provider execution metadata is not model-authored semantic evidence.
    # Keep it separate from the semantic_analysis contract and risk fields.
    provider_execution = semantic_result.pop("provider_execution", None)

    changes = semantic_result.get(
        "changes",
        [],
    )

    overall_risk = _normalize_risk(
        semantic_result.get("overall_risk"),
        default=RISK_MEDIUM,
    )

    summary = str(
        semantic_result.get(
            "summary",
            "Content modification detected.",
        )
    ).strip()

    delta_json = {
        "change_detected": True,
        "old_hash": old_snapshot.content_hash,
        "new_hash": new_snapshot.content_hash,
        "similarity": _calculate_similarity(
            old_text,
            new_text,
        ),
        "chunks_analyzed": len(changed_old),
        "semantic_analysis": semantic_result,
        "detected_at": utc_now().isoformat(),
    }
    if isinstance(provider_execution, dict):
        delta_json["provider_execution"] = provider_execution

    logger.info(
        "Semantic analysis completed for page %s: "
        "changes=%d risk=%s",
        page_id,
        len(changes),
        overall_risk,
    )

    return delta_json, summary


def _semantic_event_fields(
    delta_json: dict[str, Any],
    summary: str,
) -> dict[str, Any]:
    """The single validation/classification authority for initial and recovered events."""
    semantic_analysis = delta_json.get("semantic_analysis")
    if not isinstance(semantic_analysis, dict):
        raise DeepSeekSemanticDiffError("Semantic analysis result is not an object.")
    if semantic_analysis.get("analysis_status") != "success":
        raise DeepSeekSemanticDiffError("Semantic analysis did not return success.")
    # Keep one schema and coherence authority for initial detection,
    # recovery, and any alternative analysis caller. Do not silently
    # default a malformed provider result to a completed medium-risk event.
    validated = semantic_response_contract.DeepSeekSemanticDiffEngine._normalize_result(
        dict(semantic_analysis)
    )
    delta_json = dict(delta_json)
    delta_json["semantic_analysis"] = validated
    changes = validated["changes"]
    risk = validated["overall_risk"]
    execution = delta_json.get("provider_execution")
    total_tokens = (
        execution.get("total_tokens")
        if isinstance(execution, dict) and execution.get("usage_reported") is True
        else None
    )
    # Database's NOT NULL integer column uses 0 for unavailable usage. The
    # provider_execution.usage_reported flag disambiguates this from real 0.
    if type(total_tokens) is not int or not 0 <= total_tokens <= 2_147_483_647:
        total_tokens = 0
    return {
        "delta_json": delta_json,
        "summary": summary,
        "risk_level": risk,
        "confidence": _estimate_confidence(semantic_analysis),
        "impact_score": _calculate_impact_score(changes, risk),
        "category": _infer_category(changes),
        "change_type": _infer_change_type(changes),
        "changes_count": len(changes),
        "chunk_count": int(delta_json.get("chunks_analyzed", 0) or 0),
        "llm_tokens_used": total_tokens,
        "analysis_status": ANALYSIS_STATUS_COMPLETED,
        "analysis_error": None,
    }


# ---------------------------------------------------------------------------
# Drift event persistence
# ---------------------------------------------------------------------------

def create_drift_event(
    page_id: str,
    old_snapshot_id: str,
    new_snapshot_id: str,
    delta_json: dict[str, Any],
    summary: str,
    *,
    risk_level: str = RISK_MEDIUM,
    confidence: float = DEFAULT_CONFIDENCE,
    impact_score: int = 0,
    category: Optional[str] = None,
    change_type: Optional[str] = None,
    changes_count: int = 0,
    chunk_count: int = 0,
    llm_tokens_used: int = 0,
    analysis_status: str = ANALYSIS_STATUS_COMPLETED,
    analysis_error: Optional[str] = None,
    analysis_attempts: int = 0,
) -> Optional[SemanticDriftEvent]:
    """
    Persist a SemanticDriftEvent.

    The method is intentionally defensive:
    - validates the monitored page;
    - checks for an existing snapshot pair;
    - rolls back on failure;
    - never leaves a partial transaction open.
    """
    db = SessionLocal()

    try:
        page = (
            db.query(MonitoredPage)
            .filter(
                MonitoredPage.id == page_id
            )
            .first()
        )

        if not page:
            logger.error(
                "Monitored page not found: %s",
                page_id,
            )
            return None

        if str(old_snapshot_id) == str(new_snapshot_id):
            logger.error(
                "Cannot persist drift event for page %s: "
                "old and new snapshot IDs are identical: %s",
                page_id,
                old_snapshot_id,
            )
            return None

        snapshots = (
            db.query(PageSnapshot)
            .filter(
                PageSnapshot.id.in_(
                    [
                        old_snapshot_id,
                        new_snapshot_id,
                    ]
                )
            )
            .all()
        )

        snapshots_by_id = {
            str(snapshot.id): snapshot
            for snapshot in snapshots
        }

        old_snapshot = snapshots_by_id.get(
            str(old_snapshot_id)
        )
        new_snapshot = snapshots_by_id.get(
            str(new_snapshot_id)
        )

        if (
            old_snapshot is None
            or new_snapshot is None
        ):
            logger.error(
                "Cannot persist drift event for page %s: "
                "snapshot pair does not exist: %s -> %s",
                page_id,
                old_snapshot_id,
                new_snapshot_id,
            )
            return None

        for snapshot in (
            old_snapshot,
            new_snapshot,
        ):
            if (
                snapshot.monitored_page_id != page.id
                or snapshot.organization_id
                != page.organization_id
            ):
                logger.error(
                    "Cannot persist drift event for page %s: "
                    "snapshot %s belongs to page %s "
                    "and organization %s.",
                    page_id,
                    snapshot.id,
                    snapshot.monitored_page_id,
                    snapshot.organization_id,
                )
                return None

        existing_event = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.old_snapshot_id
                == old_snapshot_id,
                SemanticDriftEvent.new_snapshot_id
                == new_snapshot_id,
            )
            .first()
        )

        if existing_event:
            logger.info(
                "Drift event already exists for snapshot pair "
                "%s -> %s: %s",
                old_snapshot_id,
                new_snapshot_id,
                existing_event.id,
            )
            return existing_event

        # ---------------------------------------------------------------
        # Relationship evidence
        # ---------------------------------------------------------------
        #
        # Candidate entities are strictly scoped to the same organization
        # as the monitored page. The extractor itself excludes the source
        # entity and performs deterministic name/domain matching.
        #
        # Relationship extraction is enrichment only. Failure here must
        # never prevent the primary semantic drift event from being
        # persisted.
        entities_affected: list[str] = []

        try:
            organization_entities = (
                db.query(Entity)
                .filter(
                    Entity.organization_id
                    == page.organization_id
                )
                .all()
            )

            relationship_evidence = (
                extract_relationship_evidence(
                    semantic_delta_json=delta_json,
                    summary_text=summary,
                    source_entity_id=page.entity_id,
                    entities=organization_entities,
                )
            )

            entities_affected = list(
                relationship_evidence.get(
                    "entities_affected",
                    [],
                )
                or []
            )

            logger.debug(
                "Relationship evidence extracted for page %s: "
                "matched_count=%s method=%s",
                page_id,
                relationship_evidence.get(
                    "matched_count",
                    0,
                ),
                relationship_evidence.get(
                    "method",
                ),
            )

        except Exception:
            logger.exception(
                "Relationship evidence extraction failed for page %s. "
                "Persisting drift event without affected entities.",
                page_id,
            )
        event = SemanticDriftEvent(
            monitored_page_id=page_id,
            organization_id=page.organization_id,
            old_snapshot_id=old_snapshot_id,
            new_snapshot_id=new_snapshot_id,
            semantic_delta_json=delta_json,
            summary_text=summary,
            entities_affected=entities_affected,
            risk_level=_normalize_risk(
                risk_level,
                default=RISK_MEDIUM,
            ),
            confidence=_safe_float(
                confidence,
                default=DEFAULT_CONFIDENCE,
            ),
            impact_score=max(
                0,
                min(int(impact_score), 100),
            ),
            category=category,
            change_type=change_type,
            changes_count=max(
                0,
                int(changes_count),
            ),
            chunk_count=max(
                0,
                int(chunk_count),
            ),
            llm_tokens_used=max(
                0,
                int(llm_tokens_used),
            ),
            analysis_status=analysis_status,
            analysis_error=analysis_error,
            analyzed_at=(
                utc_now() if analysis_attempts > 0 else None
            ),
            analysis_attempts=max(
                0,
                int(analysis_attempts),
            ),
            detected_at=utc_now(),
        )

        try:
            with db.begin_nested():
                db.add(event)
                db.flush()

        except IntegrityError:
            existing_event = (
                db.query(SemanticDriftEvent)
                .filter(
                    SemanticDriftEvent.old_snapshot_id
                    == old_snapshot_id,
                    SemanticDriftEvent.new_snapshot_id
                    == new_snapshot_id,
                )
                .first()
            )

            if existing_event is None:
                raise

            logger.info(
                "Concurrent drift event already persisted for "
                "snapshot pair %s -> %s: %s",
                old_snapshot_id,
                new_snapshot_id,
                existing_event.id,
            )

            return existing_event

        # ---------------------------------------------------------------
        # Entity relationship graph persistence
        # ---------------------------------------------------------------
        #
        # Relationship persistence is enrichment only. A failure here must
        # not prevent the primary semantic drift event from being committed.
        #
        # Use a nested transaction/savepoint so graph writes can be rolled
        # back independently while preserving the outer event transaction.
        try:
            with db.begin_nested():
                graph_result = persist_event_relationships(
                    db=db,
                    event=event,
                    source_entity_id=page.entity_id,
                    target_entity_ids=entities_affected,
                )

            logger.debug(
                "Relationship graph persisted for event %s: "
                "created=%s updated=%s skipped=%s "
                "observations_created=%s observations_existing=%s",
                event.id,
                graph_result.created,
                graph_result.updated,
                graph_result.skipped,
                graph_result.observations_created,
                graph_result.observations_existing,
            )

        except Exception:
            logger.exception(
                "Relationship graph persistence failed for drift event %s. "
                "Persisting drift event without graph enrichment.",
                event.id,
            )

        db.commit()
        db.refresh(event)

        logger.info(
            "Drift event persisted successfully: %s "
            "(status=%s, risk=%s)",
            event.id,
            event.analysis_status,
            event.risk_level,
        )

        return event

    except Exception:
        logger.exception(
            "Failed to persist drift event for page %s.",
            page_id,
        )

        db.rollback()
        return None

    finally:
        db.close()


# ---------------------------------------------------------------------------
# Failed analysis persistence
# ---------------------------------------------------------------------------

def _create_failed_analysis_event(
    page_id: str,
    old_snapshot: PageSnapshot,
    new_snapshot: PageSnapshot,
    similarity: float,
    chunk_count: int,
    error: Exception,
) -> Optional[SemanticDriftEvent]:
    """
    Persist an explicit failed analysis.

    IMPORTANT:
    This event is NOT marked as completed and does not receive a
    fabricated semantic risk score.
    """
    error_message = str(error).strip()

    if not error_message:
        error_message = error.__class__.__name__

    delta_json = {
        "change_detected": True,
        "old_hash": old_snapshot.content_hash,
        "new_hash": new_snapshot.content_hash,
        "similarity": similarity,
        "chunks_analyzed": chunk_count,
        "semantic_analysis": {
            "analysis_status": "failed",
        },
        "analysis_failure": {
            "retryable": not isinstance(error, DeepSeekPermanentError),
            "code": (
                "permanent" if isinstance(error, DeepSeekPermanentError)
                else "transient_or_invalid_response"
            ),
        },
        "detected_at": utc_now().isoformat(),
    }

    return create_drift_event(
        page_id=page_id,
        old_snapshot_id=str(old_snapshot.id),
        new_snapshot_id=str(new_snapshot.id),
        delta_json=delta_json,
        summary=(
            "Semantic analysis failed. "
            "The content change requires re-analysis."
        ),
        risk_level=RISK_MEDIUM,
        confidence=0.0,
        impact_score=0,
        category=None,
        change_type="modification",
        changes_count=0,
        chunk_count=chunk_count,
        llm_tokens_used=0,
        analysis_status=ANALYSIS_STATUS_FAILED,
        analysis_error=error_message,
        analysis_attempts=1,
    )


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def check_and_create_drift(
    page_id: str,
) -> Optional[SemanticDriftEvent]:
    (
        has_change,
        old_snapshot,
        new_snapshot,
    ) = detect_changes_for_page(page_id)

    if (
        not has_change
        or old_snapshot is None
        or new_snapshot is None
    ):
        return None

    old_path = old_snapshot.raw_content_path
    new_path = new_snapshot.raw_content_path

    if not old_path or not os.path.isfile(old_path):
        logger.error(
            "Previous snapshot file is missing: "
            "snapshot=%s path=%s",
            old_snapshot.id,
            old_path,
        )
        return None

    if not new_path or not os.path.isfile(new_path):
        logger.error(
            "Current snapshot file is missing: "
            "snapshot=%s path=%s",
            new_snapshot.id,
            new_path,
        )
        return None

    # Protect against duplicate processing before doing expensive work.
    existing_event = _find_existing_event(
        str(old_snapshot.id),
        str(new_snapshot.id),
    )

    if existing_event:
        logger.info(
            "Snapshot pair already processed: "
            "%s -> %s (event=%s, status=%s)",
            old_snapshot.id,
            new_snapshot.id,
            existing_event.id,
            existing_event.analysis_status,
        )
        return existing_event

    try:
        with open(
            old_path,
            "r",
            encoding="utf-8",
        ) as old_file:
            old_text = old_file.read()

        with open(
            new_path,
            "r",
            encoding="utf-8",
        ) as new_file:
            new_text = new_file.read()

        if not old_text.strip() or not new_text.strip():
            logger.error(
                "Empty snapshot content for page %s.",
                page_id,
            )
            return None

        similarity = _calculate_similarity(
            old_text,
            new_text,
        )

        # Text similarity is descriptive metadata, never a semantic-risk
        # classification gate. A one-word negation in a long document can
        # change policy meaning while preserving >99% text similarity.
        logger.info(
            "Analyzing content change for page %s "
            "(text_similarity=%.4f).",
            page_id,
            similarity,
        )

        try:
            delta_json, summary = _process_semantic_analysis(
                page_id=page_id,
                old_snapshot=old_snapshot,
                new_snapshot=new_snapshot,
                old_text=old_text,
                new_text=new_text,
            )
            fields = _semantic_event_fields(delta_json, summary)
        except DeepSeekSemanticDiffError as exc:
            logger.error("Semantic analysis failed for page %s: %s", page_id, exc)
            changed_old, _ = TextChunker.find_changed_regions_with_context(
                old_text, new_text, context_chars=240,
            )
            return _create_failed_analysis_event(
                page_id=page_id,
                old_snapshot=old_snapshot,
                new_snapshot=new_snapshot,
                similarity=similarity,
                chunk_count=len(changed_old),
                error=exc,
            )

        return create_drift_event(
            page_id=page_id,
            old_snapshot_id=str(old_snapshot.id),
            new_snapshot_id=str(new_snapshot.id),
            analysis_attempts=1,
            **fields,
        )

    except FileNotFoundError:
        logger.exception(
            "Snapshot file disappeared during processing "
            "for page %s.",
            page_id,
        )
        return None

    except Exception:
        logger.exception(
            "Unexpected error during drift detection "
            "for page %s.",
            page_id,
        )
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_cli_parser() -> argparse.ArgumentParser:
    """
    Build CLI parser for targeted drift detection.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Run production semantic drift detection "
            "for a specific monitored page."
        )
    )

    group = parser.add_mutually_exclusive_group(
        required=True
    )

    group.add_argument(
        "--page-id",
        dest="page_id",
        help="UUID of the monitored page.",
    )

    group.add_argument(
        "--url",
        dest="url",
        help="Exact monitored page URL.",
    )

    return parser


def main() -> None:
    """
    Execute targeted drift detection from the command line.
    """
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s - "
            "%(name)s - "
            "%(levelname)s - "
            "%(message)s"
        ),
    )

    parser = _build_cli_parser()
    args = parser.parse_args()

    db = SessionLocal()

    try:
        query = db.query(MonitoredPage)

        if args.page_id:
            page = (
                query
                .filter(
                    MonitoredPage.id == args.page_id
                )
                .first()
            )
        else:
            page = (
                query
                .filter(
                    MonitoredPage.url == args.url
                )
                .first()
            )

        if not page:
            logger.error(
                "Monitored page not found."
            )
            return

        logger.info(
            "Running drift detection for page: %s",
            page.url,
        )

        result = check_and_create_drift(
            str(page.id)
        )

        if result:
            logger.info(
                "Drift processing completed: "
                "event=%s status=%s risk=%s",
                result.id,
                result.analysis_status,
                result.risk_level,
            )
        else:
            logger.info(
                "No new drift event was created."
            )

    finally:
        db.close()


if __name__ == "__main__":
    main()
