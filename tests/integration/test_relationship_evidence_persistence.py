from __future__ import annotations

import os
import threading
import uuid

import pytest

from src.core.database import SessionLocal
from src.detector.change_detector import create_drift_event
from src.models.entity import Entity
from src.models.monitored_page import MonitoredPage
from src.models.page_snapshot import PageSnapshot
from src.models.semantic_drift_event import SemanticDriftEvent


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="Database integration tests are disabled.",
)


def _find_source_page(
    db,
) -> MonitoredPage:
    """
    Find an existing monitored page that can provide organization
    and source-entity ownership for this integration test.
    """
    page = (
        db.query(MonitoredPage)
        .join(
            Entity,
            Entity.id == MonitoredPage.entity_id,
        )
        .order_by(MonitoredPage.created_at.asc())
        .first()
    )

    if page is None:
        pytest.skip(
            "No MonitoredPage with an Entity is available "
            "for integration testing."
        )

    return page


def _create_secondary_page(
    db,
    *,
    source_page: MonitoredPage,
) -> MonitoredPage:
    """
    Create a disposable page in the same organization and entity.
    """
    page = MonitoredPage(
        organization_id=source_page.organization_id,
        entity_id=source_page.entity_id,
        url=(
            "https://relationship-provenance-"
            f"{uuid.uuid4().hex}.example.test"
        ),
        page_type=source_page.page_type,
        crawl_frequency="daily",
        is_active=True,
    )

    db.add(page)
    db.commit()
    db.refresh(page)

    return page


def _create_snapshot(
    db,
    *,
    page: MonitoredPage,
    label: str,
) -> PageSnapshot:
    """
    Create a disposable snapshot belonging to the source page.
    """
    snapshot = PageSnapshot(
        monitored_page_id=page.id,
        organization_id=page.organization_id,
        content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        raw_content=(
            "Relationship evidence integration test "
            f"snapshot {label}."
        ),
        content_size=64,
        raw_content_path=None,
        embedding_status="pending",
        embedding_attempts=0,
    )

    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    return snapshot


def _create_target_entity(
    db,
    *,
    organization_id,
) -> Entity:
    """
    Create a uniquely named disposable target entity.

    Both name and domain are random so the deterministic extractor
    cannot accidentally match unrelated existing test data.
    """
    token = uuid.uuid4().hex

    entity = Entity(
        organization_id=organization_id,
        name=f"RelationshipTarget-{token}",
        domain=f"relationship-{token}.example.test",
        category="integration_test",
    )

    db.add(entity)
    db.commit()
    db.refresh(entity)

    return entity


def _create_unmentioned_entity(
    db,
    *,
    organization_id,
) -> Entity:
    """
    Create another same-organization entity that must not be matched.
    """
    token = uuid.uuid4().hex

    entity = Entity(
        organization_id=organization_id,
        name=f"UnmentionedTarget-{token}",
        domain=f"unmentioned-{token}.example.test",
        category="integration_test",
    )

    db.add(entity)
    db.commit()
    db.refresh(entity)

    return entity


def _delete_event(
    db,
    event_id,
) -> None:
    db.rollback()

    event = (
        db.query(SemanticDriftEvent)
        .filter(
            SemanticDriftEvent.id == event_id
        )
        .first()
    )

    if event is not None:
        db.delete(event)
        db.commit()


def _delete_snapshot(
    db,
    snapshot_id,
) -> None:
    db.rollback()

    snapshot = (
        db.query(PageSnapshot)
        .filter(
            PageSnapshot.id == snapshot_id
        )
        .first()
    )

    if snapshot is not None:
        db.delete(snapshot)
        db.commit()


def _delete_page(
    db,
    page_id,
) -> None:
    db.rollback()

    page = (
        db.query(MonitoredPage)
        .filter(
            MonitoredPage.id == page_id
        )
        .first()
    )

    if page is not None:
        db.delete(page)
        db.commit()


def _delete_entity(
    db,
    entity_id,
) -> None:
    db.rollback()

    entity = (
        db.query(Entity)
        .filter(
            Entity.id == entity_id
        )
        .first()
    )

    if entity is not None:
        db.delete(entity)
        db.commit()


def test_create_drift_event_persists_relationship_evidence():
    """
    Verify the complete persistence path:

        create_drift_event
            -> organization-scoped candidate entities
            -> deterministic relationship extraction
            -> SemanticDriftEvent.entities_affected
            -> PostgreSQL persistence

    The source entity must be excluded and an unrelated entity from
    the same organization must not be included.
    """
    db = SessionLocal()

    old_snapshot_id = None
    new_snapshot_id = None
    target_entity_id = None
    unmentioned_entity_id = None
    event_id = None

    try:
        page = _find_source_page(
            db
        )

        source_entity = (
            db.query(Entity)
            .filter(
                Entity.id == page.entity_id
            )
            .one()
        )

        target_entity = _create_target_entity(
            db,
            organization_id=page.organization_id,
        )

        target_entity_id = target_entity.id

        unmentioned_entity = _create_unmentioned_entity(
            db,
            organization_id=page.organization_id,
        )

        unmentioned_entity_id = unmentioned_entity.id

        old_snapshot = _create_snapshot(
            db,
            page=page,
            label="old",
        )

        old_snapshot_id = old_snapshot.id

        new_snapshot = _create_snapshot(
            db,
            page=page,
            label="new",
        )

        new_snapshot_id = new_snapshot.id

        delta_json = {
            "change_detected": True,
            "semantic_analysis": {
                "analysis_status": "success",
                "overall_risk": "medium",
                "changes": [
                    {
                        "type": "integration_test",
                        "description": (
                            "A dependency involving "
                            f"{target_entity.name} changed."
                        ),
                    }
                ],
            },
        }

        summary = (
            "Relationship persistence integration test. "
            f"The change explicitly affects {target_entity.name}."
        )

        event = create_drift_event(
            page_id=str(page.id),
            old_snapshot_id=str(old_snapshot.id),
            new_snapshot_id=str(new_snapshot.id),
            delta_json=delta_json,
            summary=summary,
            risk_level="medium",
            confidence=0.9,
            impact_score=50,
            category="integration_test",
            change_type="integration_test",
            changes_count=1,
            chunk_count=1,
            llm_tokens_used=0,
            analysis_status="completed",
            analysis_error=None,
            analysis_attempts=0,
        )

        assert event is not None

        event_id = event.id

        assert event.entities_affected is not None
        assert isinstance(
            event.entities_affected,
            list,
        )

        assert (
            str(target_entity.id)
            in event.entities_affected
        )

        assert (
            str(source_entity.id)
            not in event.entities_affected
        )

        assert (
            str(unmentioned_entity.id)
            not in event.entities_affected
        )

        # ---------------------------------------------------------------
        # Verify PostgreSQL persistence rather than only ORM state.
        # ---------------------------------------------------------------

        db.expire_all()

        persisted = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id == event_id
            )
            .one()
        )

        assert persisted.entities_affected is not None

        assert isinstance(
            persisted.entities_affected,
            list,
        )

        assert (
            str(target_entity.id)
            in persisted.entities_affected
        )

        assert (
            str(source_entity.id)
            not in persisted.entities_affected
        )

        assert (
            str(unmentioned_entity.id)
            not in persisted.entities_affected
        )

        assert persisted.entities_affected == [
            str(target_entity.id)
        ]

    finally:
        # Delete dependent drift event first because it references
        # the disposable snapshots.
        if event_id is not None:
            _delete_event(
                db,
                event_id,
            )

        if new_snapshot_id is not None:
            _delete_snapshot(
                db,
                new_snapshot_id,
            )

        if old_snapshot_id is not None:
            _delete_snapshot(
                db,
                old_snapshot_id,
            )

        if unmentioned_entity_id is not None:
            _delete_entity(
                db,
                unmentioned_entity_id,
            )

        if target_entity_id is not None:
            _delete_entity(
                db,
                target_entity_id,
            )

        db.close()


def test_create_drift_event_rejects_snapshot_from_different_page():
    """
    A drift event may only reference snapshots owned by its page.
    """
    db = SessionLocal()

    old_snapshot_id = None
    foreign_snapshot_id = None
    secondary_page_id = None

    try:
        page = _find_source_page(
            db
        )

        secondary_page = _create_secondary_page(
            db,
            source_page=page,
        )
        secondary_page_id = secondary_page.id

        old_snapshot = _create_snapshot(
            db,
            page=page,
            label="provenance-source",
        )
        old_snapshot_id = old_snapshot.id

        foreign_snapshot = _create_snapshot(
            db,
            page=secondary_page,
            label="provenance-foreign-page",
        )
        foreign_snapshot_id = foreign_snapshot.id

        event = create_drift_event(
            page_id=str(page.id),
            old_snapshot_id=str(old_snapshot.id),
            new_snapshot_id=str(foreign_snapshot.id),
            delta_json={
                "change_detected": True,
            },
            summary="Invalid cross-page provenance.",
            risk_level="medium",
            confidence=0.9,
            impact_score=50,
            category="integration_test",
            change_type="integration_test",
            changes_count=1,
            chunk_count=1,
            llm_tokens_used=0,
            analysis_status="completed",
            analysis_error=None,
            analysis_attempts=0,
        )

        assert event is None

        persisted = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.old_snapshot_id
                == old_snapshot.id,
                SemanticDriftEvent.new_snapshot_id
                == foreign_snapshot.id,
            )
            .first()
        )

        assert persisted is None

    finally:
        if foreign_snapshot_id is not None:
            _delete_snapshot(
                db,
                foreign_snapshot_id,
            )

        if old_snapshot_id is not None:
            _delete_snapshot(
                db,
                old_snapshot_id,
            )

        if secondary_page_id is not None:
            _delete_page(
                db,
                secondary_page_id,
            )


def test_create_drift_event_survives_graph_persistence_failure(
    monkeypatch,
):
    """
    Graph persistence is enrichment only.

    If relationship graph persistence fails after the drift event has
    received its UUID, the nested transaction/savepoint must roll back
    graph enrichment without rolling back the primary drift event.

    The committed event must still retain the deterministic
    entities_affected evidence extracted before graph persistence.
    """
    db = SessionLocal()

    old_snapshot_id = None
    new_snapshot_id = None
    target_entity_id = None
    event_id = None

    try:
        page = _find_source_page(
            db
        )

        target_entity = _create_target_entity(
            db,
            organization_id=page.organization_id,
        )
        target_entity_id = target_entity.id

        old_snapshot = _create_snapshot(
            db,
            page=page,
            label="graph-failure-old",
        )
        old_snapshot_id = old_snapshot.id

        new_snapshot = _create_snapshot(
            db,
            page=page,
            label="graph-failure-new",
        )
        new_snapshot_id = new_snapshot.id

        delta_json = {
            "change_detected": True,
            "semantic_analysis": {
                "analysis_status": "success",
                "overall_risk": "medium",
                "changes": [
                    {
                        "type": "integration_test",
                        "description": (
                            "A dependency involving "
                            f"{target_entity.name} changed."
                        ),
                    },
                ],
            },
        }

        summary = (
            "Graph persistence failure isolation test. "
            f"The change explicitly affects {target_entity.name}."
        )

        def _raise_graph_persistence_failure(
            **_kwargs,
        ):
            raise RuntimeError(
                "intentional graph persistence failure"
            )

        monkeypatch.setattr(
            "src.detector.change_detector."
            "persist_event_relationships",
            _raise_graph_persistence_failure,
        )

        event = create_drift_event(
            page_id=str(page.id),
            old_snapshot_id=str(old_snapshot.id),
            new_snapshot_id=str(new_snapshot.id),
            delta_json=delta_json,
            summary=summary,
            risk_level="medium",
            confidence=0.9,
            impact_score=50,
            category="integration_test",
            change_type="integration_test",
            changes_count=1,
            chunk_count=1,
            llm_tokens_used=0,
            analysis_status="completed",
            analysis_error=None,
            analysis_attempts=0,
        )

        assert event is not None

        event_id = event.id

        assert event_id is not None

        # Relationship extraction happens before graph persistence and
        # must remain part of the primary drift event even when graph
        # enrichment fails.
        assert event.entities_affected == [
            str(target_entity.id)
        ]

        # Verify the primary event was actually committed to PostgreSQL,
        # rather than merely surviving in the current ORM identity map.
        db.expire_all()

        persisted = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id == event_id
            )
            .one()
        )

        assert persisted.entities_affected == [
            str(target_entity.id)
        ]

    finally:
        if event_id is not None:
            _delete_event(
                db,
                event_id,
            )

        if new_snapshot_id is not None:
            _delete_snapshot(
                db,
                new_snapshot_id,
            )

        if old_snapshot_id is not None:
            _delete_snapshot(
                db,
                old_snapshot_id,
            )

        if target_entity_id is not None:
            _delete_entity(
                db,
                target_entity_id,
            )

        db.close()


def test_create_drift_event_rejects_identical_snapshot_pair():
    """
    A drift event must represent two distinct document versions.
    """
    db = SessionLocal()

    snapshot_id = None

    try:
        page = _find_source_page(
            db
        )

        snapshot = _create_snapshot(
            db,
            page=page,
            label="identical-pair",
        )
        snapshot_id = snapshot.id

        event = create_drift_event(
            page_id=str(page.id),
            old_snapshot_id=str(snapshot.id),
            new_snapshot_id=str(snapshot.id),
            delta_json={
                "change_detected": True,
            },
            summary="Invalid identical snapshot pair.",
            risk_level="medium",
            confidence=0.9,
            impact_score=50,
            category="integration_test",
            change_type="integration_test",
            changes_count=1,
            chunk_count=1,
            llm_tokens_used=0,
            analysis_status="completed",
            analysis_error=None,
            analysis_attempts=0,
        )

        assert event is None

        persisted = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.old_snapshot_id
                == snapshot.id,
                SemanticDriftEvent.new_snapshot_id
                == snapshot.id,
            )
            .first()
        )

        assert persisted is None

    finally:
        if snapshot_id is not None:
            _delete_snapshot(
                db,
                snapshot_id,
            )


def test_create_drift_event_concurrent_identical_pair_is_idempotent():
    """
    Concurrent persistence of the same snapshot pair must converge
    on one durable semantic drift event.
    """
    setup_db = SessionLocal()

    old_snapshot_id = None
    new_snapshot_id = None
    event_id = None

    try:
        page = _find_source_page(
            setup_db
        )
        page_id = page.id

        old_snapshot = _create_snapshot(
            setup_db,
            page=page,
            label="concurrent-old",
        )
        old_snapshot_id = old_snapshot.id

        new_snapshot = _create_snapshot(
            setup_db,
            page=page,
            label="concurrent-new",
        )
        new_snapshot_id = new_snapshot.id

        barrier = threading.Barrier(2)
        results = []
        errors = []
        lock = threading.Lock()

        def persist() -> None:
            try:
                barrier.wait()

                result = create_drift_event(
                    page_id=str(page_id),
                    old_snapshot_id=str(old_snapshot_id),
                    new_snapshot_id=str(new_snapshot_id),
                    delta_json={
                        "change_detected": True,
                    },
                    summary="Concurrent identical drift event.",
                    risk_level="medium",
                    confidence=0.9,
                    impact_score=50,
                    category="integration_test",
                    change_type="integration_test",
                    changes_count=1,
                    chunk_count=1,
                    llm_tokens_used=0,
                    analysis_status="completed",
                    analysis_error=None,
                    analysis_attempts=0,
                )

                with lock:
                    results.append(result)

            except BaseException as exc:
                with lock:
                    errors.append(exc)

        threads = [
            threading.Thread(
                target=persist
            ),
            threading.Thread(
                target=persist
            ),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(
                timeout=10
            )

        assert all(
            not thread.is_alive()
            for thread in threads
        )
        assert errors == []
        assert len(results) == 2
        assert all(
            result is not None
            for result in results
        )

        result_ids = {
            result.id
            for result in results
        }

        assert len(result_ids) == 1

        event_id = next(
            iter(result_ids)
        )

        verify_db = SessionLocal()

        try:
            rows = (
                verify_db.query(
                    SemanticDriftEvent
                )
                .filter(
                    SemanticDriftEvent.old_snapshot_id
                    == old_snapshot_id,
                    SemanticDriftEvent.new_snapshot_id
                    == new_snapshot_id,
                )
                .all()
            )

            assert len(rows) == 1
            assert rows[0].id == event_id

        finally:
            verify_db.close()

    finally:
        setup_db.close()

        cleanup_db = SessionLocal()

        try:
            if event_id is not None:
                _delete_event(
                    cleanup_db,
                    event_id,
                )

            if new_snapshot_id is not None:
                _delete_snapshot(
                    cleanup_db,
                    new_snapshot_id,
                )

            if old_snapshot_id is not None:
                _delete_snapshot(
                    cleanup_db,
                    old_snapshot_id,
                )

        finally:
            cleanup_db.close()
