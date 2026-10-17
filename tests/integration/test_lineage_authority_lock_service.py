from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

from src.core.database import SessionLocal
from src.services.lineage_authority_lock_service import (
    acquire_lineage_policy_authority_lock,
    acquire_lineage_target_authority_lock,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def test_same_target_authority_blocks_until_transaction_releases():
    organization_id = uuid.uuid4()

    first_db = SessionLocal()
    second_db = SessionLocal()

    acquired = threading.Event()
    completed = threading.Event()
    errors: list[BaseException] = []

    try:
        acquire_lineage_target_authority_lock(
            first_db,
            organization_id=organization_id,
            source_external_id="source-a",
            asset_external_id="index-a",
        )

        def acquire_same_lock() -> None:
            try:
                acquired.set()

                acquire_lineage_target_authority_lock(
                    second_db,
                    organization_id=organization_id,
                    source_external_id="source-a",
                    asset_external_id="index-a",
                )

                completed.set()
            except BaseException as exc:
                errors.append(exc)
                completed.set()

        thread = threading.Thread(
            target=acquire_same_lock,
        )
        thread.start()

        assert acquired.wait(timeout=1.0)
        time.sleep(0.2)
        assert completed.is_set() is False

        first_db.commit()

        assert completed.wait(timeout=2.0)
        thread.join(timeout=2.0)

        assert errors == []

    finally:
        first_db.rollback()
        second_db.rollback()
        first_db.close()
        second_db.close()


def test_different_target_authorities_do_not_block_each_other():
    organization_id = uuid.uuid4()

    first_db = SessionLocal()
    second_db = SessionLocal()

    completed = threading.Event()
    errors: list[BaseException] = []

    try:
        acquire_lineage_target_authority_lock(
            first_db,
            organization_id=organization_id,
            source_external_id="source-a",
            asset_external_id="index-a",
        )

        def acquire_different_lock() -> None:
            try:
                acquire_lineage_target_authority_lock(
                    second_db,
                    organization_id=organization_id,
                    source_external_id="source-a",
                    asset_external_id="index-b",
                )

                completed.set()
            except BaseException as exc:
                errors.append(exc)
                completed.set()

        thread = threading.Thread(
            target=acquire_different_lock,
        )
        thread.start()

        assert completed.wait(timeout=2.0)
        thread.join(timeout=2.0)

        assert errors == []

    finally:
        first_db.rollback()
        second_db.rollback()
        first_db.close()
        second_db.close()


def test_policy_authority_uses_same_logical_key_across_sessions():
    organization_id = uuid.uuid4()

    first_db = SessionLocal()
    second_db = SessionLocal()

    completed = threading.Event()
    errors: list[BaseException] = []

    try:
        acquire_lineage_policy_authority_lock(
            first_db,
            organization_id=organization_id,
            policy_external_id="rag-invalidation",
        )

        def acquire_same_policy_lock() -> None:
            try:
                acquire_lineage_policy_authority_lock(
                    second_db,
                    organization_id=organization_id,
                    policy_external_id="rag-invalidation",
                )

                completed.set()
            except BaseException as exc:
                errors.append(exc)
                completed.set()

        thread = threading.Thread(
            target=acquire_same_policy_lock,
        )
        thread.start()

        time.sleep(0.2)
        assert completed.is_set() is False

        first_db.rollback()

        assert completed.wait(timeout=2.0)
        thread.join(timeout=2.0)

        assert errors == []

    finally:
        first_db.rollback()
        second_db.rollback()
        first_db.close()
        second_db.close()


def test_policy_and_target_namespaces_do_not_alias():
    organization_id = uuid.uuid4()

    first_db = SessionLocal()
    second_db = SessionLocal()

    completed = threading.Event()
    errors: list[BaseException] = []

    try:
        acquire_lineage_policy_authority_lock(
            first_db,
            organization_id=organization_id,
            policy_external_id="shared-id",
        )

        def acquire_target_lock() -> None:
            try:
                acquire_lineage_target_authority_lock(
                    second_db,
                    organization_id=organization_id,
                    source_external_id="source-a",
                    asset_external_id="shared-id",
                )

                completed.set()
            except BaseException as exc:
                errors.append(exc)
                completed.set()

        thread = threading.Thread(
            target=acquire_target_lock,
        )
        thread.start()

        assert completed.wait(timeout=2.0)
        thread.join(timeout=2.0)

        assert errors == []

    finally:
        first_db.rollback()
        second_db.rollback()
        first_db.close()
        second_db.close()


def test_target_authority_is_tenant_scoped():
    first_organization_id = uuid.uuid4()
    second_organization_id = uuid.uuid4()

    first_db = SessionLocal()
    second_db = SessionLocal()

    completed = threading.Event()
    errors: list[BaseException] = []

    try:
        acquire_lineage_target_authority_lock(
            first_db,
            organization_id=first_organization_id,
            source_external_id="source-a",
            asset_external_id="index-a",
        )

        def acquire_other_tenant_lock() -> None:
            try:
                acquire_lineage_target_authority_lock(
                    second_db,
                    organization_id=second_organization_id,
                    source_external_id="source-a",
                    asset_external_id="index-a",
                )

                completed.set()
            except BaseException as exc:
                errors.append(exc)
                completed.set()

        thread = threading.Thread(
            target=acquire_other_tenant_lock,
        )
        thread.start()

        assert completed.wait(timeout=2.0)
        thread.join(timeout=2.0)

        assert errors == []

    finally:
        first_db.rollback()
        second_db.rollback()
        first_db.close()
        second_db.close()


@pytest.mark.parametrize(
    (
        "source_external_id",
        "asset_external_id",
    ),
    (
        (" source-a ", " index-a "),
        ("source-a", "index-a"),
    ),
)
def test_target_authority_normalizes_external_identity(
    source_external_id,
    asset_external_id,
):
    organization_id = uuid.uuid4()

    first_db = SessionLocal()
    second_db = SessionLocal()

    completed = threading.Event()
    errors: list[BaseException] = []

    try:
        acquire_lineage_target_authority_lock(
            first_db,
            organization_id=organization_id,
            source_external_id="source-a",
            asset_external_id="index-a",
        )

        def acquire_normalized_lock() -> None:
            try:
                acquire_lineage_target_authority_lock(
                    second_db,
                    organization_id=organization_id,
                    source_external_id=source_external_id,
                    asset_external_id=asset_external_id,
                )

                completed.set()
            except BaseException as exc:
                errors.append(exc)
                completed.set()

        thread = threading.Thread(
            target=acquire_normalized_lock,
        )
        thread.start()

        time.sleep(0.2)
        assert completed.is_set() is False

        first_db.commit()

        assert completed.wait(timeout=2.0)
        thread.join(timeout=2.0)

        assert errors == []

    finally:
        first_db.rollback()
        second_db.rollback()
        first_db.close()
        second_db.close()
