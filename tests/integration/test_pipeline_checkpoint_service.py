from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from src.core.database import SessionLocal
from src.models.organization import Organization
from src.models.pipeline_checkpoint import PipelineCheckpoint
from src.services.pipeline_checkpoint_service import (
    persist_pipeline_checkpoint,
)


PATTERN_PIPELINE = "pattern_detection_v1"
ANOMALY_PIPELINE = "anomaly_detection_v1"


def _make_organization(db):
    suffix = uuid.uuid4().hex[:10]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"pipeline-checkpoint-{suffix}",
        slug=f"pipeline-checkpoint-{suffix}",
        billing_email=f"{suffix}@example.com",
    )

    db.add(organization)
    db.flush()

    return organization


def _cleanup(
    db,
    organization_id,
):
    if organization_id is None:
        return

    db.rollback()

    db.execute(
        delete(PipelineCheckpoint).where(
            PipelineCheckpoint.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(Organization).where(
            Organization.id
            == organization_id
        )
    )

    db.commit()


def _get_checkpoint(
    db,
    *,
    organization_id,
    pipeline_name,
):
    return (
        db.query(PipelineCheckpoint)
        .filter(
            PipelineCheckpoint.organization_id
            == organization_id,
            PipelineCheckpoint.pipeline_name
            == pipeline_name,
        )
        .one_or_none()
    )


def test_insert_first_pipeline_checkpoint():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        source_created_at = datetime(
            2026,
            9,
            9,
            1,
            0,
            tzinfo=timezone.utc,
        )
        knowledge_cursor = datetime(
            2026,
            9,
            8,
            23,
            0,
            tzinfo=timezone.utc,
        )
        source_record_id = uuid.uuid4()

        result = persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PATTERN_PIPELINE,
            source_created_at=source_created_at,
            source_record_id=source_record_id,
            knowledge_cursor=knowledge_cursor,
            metadata_json={
                "created_patterns": 0,
            },
        )

        db.commit()

        assert result.advanced is True
        assert result.organization_id == organization.id
        assert result.pipeline_name == PATTERN_PIPELINE
        assert result.source_created_at == source_created_at
        assert result.source_record_id == source_record_id
        assert result.knowledge_cursor == knowledge_cursor

        checkpoint = _get_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PATTERN_PIPELINE,
        )

        assert checkpoint is not None
        assert checkpoint.id == result.checkpoint_id
        assert checkpoint.source_created_at == source_created_at
        assert checkpoint.source_record_id == source_record_id
        assert checkpoint.knowledge_cursor == knowledge_cursor
        assert checkpoint.metadata_json == {
            "created_patterns": 0,
        }

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_newer_source_watermark_advances_checkpoint():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        first_created_at = datetime(
            2026,
            9,
            9,
            1,
            0,
            tzinfo=timezone.utc,
        )
        second_created_at = (
            first_created_at
            + timedelta(minutes=5)
        )

        first_record_id = uuid.uuid4()
        second_record_id = uuid.uuid4()

        first = persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PATTERN_PIPELINE,
            source_created_at=first_created_at,
            source_record_id=first_record_id,
            knowledge_cursor=datetime(
                2026,
                9,
                8,
                22,
                0,
                tzinfo=timezone.utc,
            ),
            metadata_json={
                "run": 1,
            },
        )

        db.commit()

        second_cursor = datetime(
            2026,
            9,
            8,
            23,
            30,
            tzinfo=timezone.utc,
        )

        second = persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PATTERN_PIPELINE,
            source_created_at=second_created_at,
            source_record_id=second_record_id,
            knowledge_cursor=second_cursor,
            metadata_json={
                "run": 2,
            },
        )

        db.commit()

        assert first.advanced is True
        assert second.advanced is True
        assert second.checkpoint_id == first.checkpoint_id

        checkpoint = _get_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PATTERN_PIPELINE,
        )

        assert checkpoint is not None
        assert checkpoint.source_created_at == second_created_at
        assert checkpoint.source_record_id == second_record_id
        assert checkpoint.knowledge_cursor == second_cursor
        assert checkpoint.metadata_json == {
            "run": 2,
        }

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_stale_source_watermark_does_not_move_checkpoint_backward():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        newer_created_at = datetime(
            2026,
            9,
            9,
            2,
            0,
            tzinfo=timezone.utc,
        )
        stale_created_at = (
            newer_created_at
            - timedelta(hours=1)
        )

        newer_record_id = uuid.uuid4()
        stale_record_id = uuid.uuid4()

        newer_cursor = datetime(
            2026,
            9,
            9,
            0,
            0,
            tzinfo=timezone.utc,
        )

        first = persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PATTERN_PIPELINE,
            source_created_at=newer_created_at,
            source_record_id=newer_record_id,
            knowledge_cursor=newer_cursor,
            metadata_json={
                "winner": True,
            },
        )

        db.commit()

        stale = persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PATTERN_PIPELINE,
            source_created_at=stale_created_at,
            source_record_id=stale_record_id,
            knowledge_cursor=datetime(
                2026,
                8,
                1,
                0,
                0,
                tzinfo=timezone.utc,
            ),
            metadata_json={
                "winner": False,
            },
        )

        db.commit()

        assert first.advanced is True
        assert stale.advanced is False
        assert stale.checkpoint_id == first.checkpoint_id

        checkpoint = _get_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PATTERN_PIPELINE,
        )

        assert checkpoint is not None
        assert checkpoint.source_created_at == newer_created_at
        assert checkpoint.source_record_id == newer_record_id
        assert checkpoint.knowledge_cursor == newer_cursor
        assert checkpoint.metadata_json == {
            "winner": True,
        }

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_equal_source_timestamp_uses_uuid_tiebreaker():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        source_created_at = datetime(
            2026,
            9,
            9,
            3,
            0,
            tzinfo=timezone.utc,
        )

        lower_record_id = uuid.UUID(
            "00000000-0000-0000-0000-000000000001"
        )
        higher_record_id = uuid.UUID(
            "ffffffff-ffff-ffff-ffff-ffffffffffff"
        )

        first = persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PATTERN_PIPELINE,
            source_created_at=source_created_at,
            source_record_id=lower_record_id,
            knowledge_cursor=datetime(
                2026,
                9,
                8,
                20,
                0,
                tzinfo=timezone.utc,
            ),
            metadata_json={
                "record": "lower",
            },
        )

        db.commit()

        second_cursor = datetime(
            2026,
            9,
            8,
            21,
            0,
            tzinfo=timezone.utc,
        )

        second = persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PATTERN_PIPELINE,
            source_created_at=source_created_at,
            source_record_id=higher_record_id,
            knowledge_cursor=second_cursor,
            metadata_json={
                "record": "higher",
            },
        )

        db.commit()

        stale = persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PATTERN_PIPELINE,
            source_created_at=source_created_at,
            source_record_id=lower_record_id,
            knowledge_cursor=datetime(
                2026,
                8,
                1,
                0,
                0,
                tzinfo=timezone.utc,
            ),
            metadata_json={
                "record": "stale-lower",
            },
        )

        db.commit()

        assert first.advanced is True
        assert second.advanced is True
        assert stale.advanced is False

        checkpoint = _get_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PATTERN_PIPELINE,
        )

        assert checkpoint is not None
        assert checkpoint.source_created_at == source_created_at
        assert checkpoint.source_record_id == higher_record_id
        assert checkpoint.knowledge_cursor == second_cursor
        assert checkpoint.metadata_json == {
            "record": "higher",
        }

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_pattern_and_anomaly_checkpoints_are_independent():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(db)
        organization_id = organization.id

        pattern_record_id = uuid.uuid4()
        anomaly_record_id = uuid.uuid4()

        pattern_created_at = datetime(
            2026,
            9,
            9,
            4,
            0,
            tzinfo=timezone.utc,
        )
        anomaly_created_at = datetime(
            2026,
            9,
            9,
            4,
            5,
            tzinfo=timezone.utc,
        )

        pattern = persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PATTERN_PIPELINE,
            source_created_at=pattern_created_at,
            source_record_id=pattern_record_id,
            knowledge_cursor=datetime(
                2026,
                9,
                8,
                23,
                0,
                tzinfo=timezone.utc,
            ),
            metadata_json={
                "stage": "pattern",
            },
        )

        anomaly = persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=ANOMALY_PIPELINE,
            source_created_at=anomaly_created_at,
            source_record_id=anomaly_record_id,
            knowledge_cursor=datetime(
                2026,
                9,
                8,
                23,
                30,
                tzinfo=timezone.utc,
            ),
            metadata_json={
                "stage": "anomaly",
            },
        )

        db.commit()

        assert pattern.advanced is True
        assert anomaly.advanced is True
        assert pattern.checkpoint_id != anomaly.checkpoint_id

        rows = (
            db.query(PipelineCheckpoint)
            .filter(
                PipelineCheckpoint.organization_id
                == organization.id
            )
            .all()
        )

        assert len(rows) == 2

        by_pipeline = {
            row.pipeline_name: row
            for row in rows
        }

        assert set(by_pipeline) == {
            PATTERN_PIPELINE,
            ANOMALY_PIPELINE,
        }

        assert (
            by_pipeline[
                PATTERN_PIPELINE
            ].source_record_id
            == pattern_record_id
        )

        assert (
            by_pipeline[
                ANOMALY_PIPELINE
            ].source_record_id
            == anomaly_record_id
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_checkpoint_write_is_rolled_back_by_caller():
    setup_db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            setup_db
        )
        organization_id = organization.id

        setup_db.commit()

        checkpoint_db = SessionLocal()

        try:
            result = persist_pipeline_checkpoint(
                checkpoint_db,
                organization_id=organization.id,
                pipeline_name=PATTERN_PIPELINE,
                source_created_at=datetime(
                    2026,
                    9,
                    9,
                    5,
                    0,
                    tzinfo=timezone.utc,
                ),
                source_record_id=uuid.uuid4(),
                knowledge_cursor=datetime(
                    2026,
                    9,
                    9,
                    0,
                    0,
                    tzinfo=timezone.utc,
                ),
                metadata_json={
                    "rollback": True,
                },
            )

            assert result.advanced is True

            checkpoint_db.rollback()

        finally:
            checkpoint_db.close()

        verify_db = SessionLocal()

        try:
            checkpoint = _get_checkpoint(
                verify_db,
                organization_id=organization.id,
                pipeline_name=PATTERN_PIPELINE,
            )

            assert checkpoint is None

        finally:
            verify_db.close()

    finally:
        try:
            _cleanup(
                setup_db,
                organization_id,
            )
        finally:
            setup_db.close()