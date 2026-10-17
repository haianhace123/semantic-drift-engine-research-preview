from __future__ import annotations

import os
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

import src.main as main_module

from src.api import temporal_routes
from src.core.database import SessionLocal
from src.core.security import (
    CSRF_COOKIE,
    get_current_user,
)
from src.main import app
from src.models.organization import Organization
from src.models.watchlist import Watchlist
from src.services.alert_policy_revision_service import (
    load_alert_policy_revision,
)


pytestmark = pytest.mark.skipif(
    os.getenv(
        "RUN_DB_INTEGRATION_TESTS"
    )
    != "1",
    reason=(
        "RUN_DB_INTEGRATION_TESTS "
        "is not enabled"
    ),
)


AUTH_HEADERS = {
    "Authorization": (
        "Bearer watchlist-api-test-token"
    ),
}


def _fake_user(
    organization_id: uuid.UUID,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        email="watchlist-api@example.com",
    )


def _database_override():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


def _allow_auth_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "verify_token",
        lambda token: {
            "sub":
                str(
                    uuid.uuid4()
                ),
        },
    )


def _override_authenticated_user(
    organization_id: uuid.UUID,
) -> None:
    app.dependency_overrides[
        get_current_user
    ] = lambda: _fake_user(
        organization_id
    )

    app.dependency_overrides[
        temporal_routes.get_db
    ] = _database_override


def _clear_overrides() -> None:
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    app.dependency_overrides.pop(
        temporal_routes.get_db,
        None,
    )


def _csrf_headers(
    client: TestClient,
) -> dict[str, str]:
    token = (
        "watchlist-api-csrf-token"
    )

    client.cookies.set(
        CSRF_COOKIE,
        token,
    )

    return {
        **AUTH_HEADERS,
        "X-CSRF-Token": token,
    }


def _create_organization(
    db,
    *,
    prefix: str,
) -> Organization:
    suffix = uuid.uuid4().hex[:10]

    organization = Organization(
        id=uuid.uuid4(),
        name=(
            f"{prefix}-{suffix}"
        ),
        slug=(
            f"{prefix}-{suffix}"
        ),
        billing_email=(
            f"{prefix}-{suffix}"
            "@example.com"
        ),
    )

    db.add(
        organization
    )

    db.flush()

    return organization


def _create_watchlist(
    db,
    *,
    organization_id: uuid.UUID,
    name: str,
) -> Watchlist:
    watchlist = Watchlist(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name=name,
        description="Original description",
        entity_ids=[],
        page_ids=[],
        categories=["legal"],
        alert_on_high_risk=True,
        alert_on_medium_risk=False,
        alert_on_pattern=True,
        alert_on_anomaly=True,
        is_active=True,
        is_archived=False,
    )

    db.add(
        watchlist
    )

    db.flush()

    return watchlist


def _cleanup(
    organization_ids: list[
        uuid.UUID
    ],
) -> None:
    db = SessionLocal()

    try:
        db.rollback()

        db.execute(
            delete(
                Watchlist
            ).where(
                Watchlist.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(
                Organization
            ).where(
                Organization.id.in_(
                    organization_ids
                )
            )
        )

        db.commit()

    finally:
        db.close()


def test_create_watchlist_returns_canonical_policy_shape(
    monkeypatch: pytest.MonkeyPatch,
):
    db = SessionLocal()
    organization_ids = []

    try:
        organization = (
            _create_organization(
                db,
                prefix="watchlist-create",
            )
        )

        organization_ids.append(
            organization.id
        )

        db.commit()

        _allow_auth_middleware(
            monkeypatch
        )

        _override_authenticated_user(
            organization.id
        )

        with TestClient(
            app
        ) as client:
            response = client.post(
                "/api/temporal/watchlist",
                headers=_csrf_headers(
                    client
                ),
                json={
                    "name":
                        "Regulatory Changes",

                    "description":
                        "Track legal changes.",

                    "categories": [
                        " legal ",
                        "legal",
                        "policy",
                    ],

                    "alert_on_high_risk":
                        True,

                    "alert_on_medium_risk":
                        False,

                    "alert_on_pattern":
                        True,

                    "alert_on_anomaly":
                        False,
                },
            )

        assert (
            response.status_code
            == 200
        ), response.text

        payload = response.json()

        assert (
            payload["name"]
            == "Regulatory Changes"
        )

        assert (
            payload["categories"]
            == [
                "legal",
                "policy",
            ]
        )

        assert (
            payload["alert_on_high_risk"]
            is True
        )

        assert (
            payload["alert_on_medium_risk"]
            is False
        )

        assert (
            payload["alert_on_pattern"]
            is True
        )

        assert (
            payload["alert_on_anomaly"]
            is False
        )

        assert (
            payload["is_active"]
            is True
        )

        assert (
            payload["is_archived"]
            is False
        )

        assert (
            payload["entities"]
            == []
        )
        verification_db = (
            SessionLocal()
        )

        try:
            revision = (
                load_alert_policy_revision(
                    verification_db,
                    organization_id=(
                        organization.id
                    ),
                )
            )

            assert revision is not None
            assert (
                revision.policy_revision
                == 1
            )
            assert (
                revision.reconciled_revision
                == 0
            )
            assert (
                revision.reconciliation_required
                is True
            )

        finally:
            verification_db.close()

    finally:
        _clear_overrides()
        db.close()

        _cleanup(
            organization_ids
        )


def test_patch_watchlist_preserves_omitted_fields_and_accepts_false(
    monkeypatch: pytest.MonkeyPatch,
):
    db = SessionLocal()
    organization_ids = []

    try:
        organization = (
            _create_organization(
                db,
                prefix="watchlist-patch",
            )
        )

        organization_ids.append(
            organization.id
        )

        watchlist = (
            _create_watchlist(
                db,
                organization_id=(
                    organization.id
                ),
                name="Original Watchlist",
            )
        )

        watchlist_id = (
            watchlist.id
        )

        db.commit()

        _allow_auth_middleware(
            monkeypatch
        )

        _override_authenticated_user(
            organization.id
        )

        with TestClient(
            app
        ) as client:
            response = client.patch(
                (
                    "/api/temporal/"
                    f"watchlist/{watchlist_id}"
                ),
                headers=_csrf_headers(
                    client
                ),
                json={
                    "name":
                        "Updated Watchlist",

                    "description":
                        None,

                    "categories": [
                        " finance ",
                        "finance",
                        "compliance",
                    ],

                    "alert_on_pattern":
                        False,

                    "alert_on_anomaly":
                        False,

                    "is_active":
                        False,
                },
            )

        assert (
            response.status_code
            == 200
        ), response.text

        payload = response.json()

        assert (
            payload["name"]
            == "Updated Watchlist"
        )

        assert (
            payload["description"]
            is None
        )

        assert (
            payload["categories"]
            == [
                "finance",
                "compliance",
            ]
        )

        assert (
            payload["alert_on_high_risk"]
            is True
        )

        assert (
            payload["alert_on_medium_risk"]
            is False
        )

        assert (
            payload["alert_on_pattern"]
            is False
        )

        assert (
            payload["alert_on_anomaly"]
            is False
        )

        assert (
            payload["is_active"]
            is False
        )

        verification_db = (
            SessionLocal()
        )

        try:
            persisted = (
                verification_db.get(
                    Watchlist,
                    watchlist_id,
                )
            )

            assert (
                persisted
                is not None
            )

            assert (
                persisted.alert_on_high_risk
                is True
            )

            assert (
                persisted.alert_on_pattern
                is False
            )

            assert (
                persisted.alert_on_anomaly
                is False
            )
            revision = (
                load_alert_policy_revision(
                    verification_db,
                    organization_id=(
                        organization.id
                    ),
                )
            )

            assert revision is not None
            assert (
                revision.policy_revision
                == 1
            )
            assert (
                revision.reconciled_revision
                == 0
            )

        finally:
            verification_db.close()

    finally:
        _clear_overrides()
        db.close()

        _cleanup(
            organization_ids
        )


def test_patch_watchlist_is_tenant_isolated(
    monkeypatch: pytest.MonkeyPatch,
):
    db = SessionLocal()
    organization_ids = []

    try:
        own_organization = (
            _create_organization(
                db,
                prefix="watchlist-own",
            )
        )

        foreign_organization = (
            _create_organization(
                db,
                prefix="watchlist-foreign",
            )
        )

        organization_ids.extend(
            [
                own_organization.id,
                foreign_organization.id,
            ]
        )

        foreign_watchlist = (
            _create_watchlist(
                db,
                organization_id=(
                    foreign_organization.id
                ),
                name="Foreign Watchlist",
            )
        )

        foreign_watchlist_id = (
            foreign_watchlist.id
        )

        db.commit()

        _allow_auth_middleware(
            monkeypatch
        )

        _override_authenticated_user(
            own_organization.id
        )

        with TestClient(
            app
        ) as client:
            response = client.patch(
                (
                    "/api/temporal/"
                    "watchlist/"
                    f"{foreign_watchlist_id}"
                ),
                headers=_csrf_headers(
                    client
                ),
                json={
                    "alert_on_anomaly":
                        False,
                },
            )

        assert (
            response.status_code
            == 404
        )

        verification_db = (
            SessionLocal()
        )

        try:
            persisted = (
                verification_db.get(
                    Watchlist,
                    foreign_watchlist_id,
                )
            )

            assert (
                persisted
                is not None
            )

            assert (
                persisted.alert_on_anomaly
                is True
            )
            own_revision = (
                load_alert_policy_revision(
                    verification_db,
                    organization_id=(
                        own_organization.id
                    ),
                )
            )

            foreign_revision = (
                load_alert_policy_revision(
                    verification_db,
                    organization_id=(
                        foreign_organization.id
                    ),
                )
            )

            assert own_revision is None
            assert foreign_revision is None

        finally:
            verification_db.close()

    finally:
        _clear_overrides()
        db.close()

        _cleanup(
            organization_ids
        )


def test_patch_archived_watchlist_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
):
    db = SessionLocal()
    organization_ids = []

    try:
        organization = (
            _create_organization(
                db,
                prefix="watchlist-archived",
            )
        )

        organization_ids.append(
            organization.id
        )

        watchlist = (
            _create_watchlist(
                db,
                organization_id=(
                    organization.id
                ),
                name="Archived Watchlist",
            )
        )

        watchlist_id = (
            watchlist.id
        )

        db.commit()

        _allow_auth_middleware(
            monkeypatch
        )

        _override_authenticated_user(
            organization.id
        )

        with TestClient(
            app
        ) as client:
            headers = (
                _csrf_headers(
                    client
                )
            )

            archive_response = (
                client.delete(
                    (
                        "/api/temporal/"
                        "watchlist/"
                        f"{watchlist_id}"
                    ),
                    headers=headers,
                )
            )

            assert (
                archive_response.status_code
                == 200
            ), archive_response.text

            patch_response = (
                client.patch(
                    (
                        "/api/temporal/"
                        "watchlist/"
                        f"{watchlist_id}"
                    ),
                    headers=headers,
                    json={
                        "alert_on_anomaly":
                            False,
                    },
                )
            )

        assert (
            patch_response.status_code
            == 409
        )

        assert (
            patch_response.json()[
                "detail"
            ]
            == (
                "Archived watchlists "
                "cannot be modified"
            )
        )
        verification_db = (
            SessionLocal()
        )

        try:
            revision = (
                load_alert_policy_revision(
                    verification_db,
                    organization_id=(
                        organization.id
                    ),
                )
            )

            assert revision is not None
            assert (
                revision.policy_revision
                == 1
            )

        finally:
            verification_db.close()

    finally:
        _clear_overrides()
        db.close()

        _cleanup(
            organization_ids
        )


def test_patch_watchlist_rejects_foreign_scope_ids(
    monkeypatch: pytest.MonkeyPatch,
):
    db = SessionLocal()
    organization_ids = []

    try:
        organization = (
            _create_organization(
                db,
                prefix="watchlist-scope",
            )
        )

        organization_ids.append(
            organization.id
        )

        watchlist = (
            _create_watchlist(
                db,
                organization_id=(
                    organization.id
                ),
                name="Scoped Watchlist",
            )
        )

        watchlist_id = (
            watchlist.id
        )

        db.commit()

        _allow_auth_middleware(
            monkeypatch
        )

        _override_authenticated_user(
            organization.id
        )

        with TestClient(
            app
        ) as client:
            response = client.patch(
                (
                    "/api/temporal/"
                    "watchlist/"
                    f"{watchlist_id}"
                ),
                headers=_csrf_headers(
                    client
                ),
                json={
                    "entity_ids": [
                        str(
                            uuid.uuid4()
                        )
                    ],
                },
            )

        assert (
            response.status_code
            == 404
        )

    finally:
        _clear_overrides()
        db.close()

        _cleanup(
            organization_ids
        )

def test_patch_watchlist_metadata_and_same_policy_values_do_not_bump_revision(
    monkeypatch: pytest.MonkeyPatch,
):
    db = SessionLocal()
    organization_ids = []

    try:
        organization = (
            _create_organization(
                db,
                prefix="watchlist-no-policy-change",
            )
        )

        organization_ids.append(
            organization.id
        )

        watchlist = (
            _create_watchlist(
                db,
                organization_id=(
                    organization.id
                ),
                name="Stable Policy",
            )
        )

        watchlist_id = (
            watchlist.id
        )

        db.commit()

        _allow_auth_middleware(
            monkeypatch
        )
        _override_authenticated_user(
            organization.id
        )

        with TestClient(
            app
        ) as client:
            headers = (
                _csrf_headers(
                    client
                )
            )

            metadata_response = (
                client.patch(
                    (
                        "/api/temporal/"
                        "watchlist/"
                        f"{watchlist_id}"
                    ),
                    headers=headers,
                    json={
                        "name":
                            "Renamed Stable Policy",
                        "description":
                            "Metadata only.",
                    },
                )
            )

            assert (
                metadata_response.status_code
                == 200
            ), metadata_response.text

            same_policy_response = (
                client.patch(
                    (
                        "/api/temporal/"
                        "watchlist/"
                        f"{watchlist_id}"
                    ),
                    headers=headers,
                    json={
                        "categories": [
                            " legal ",
                            "legal",
                        ],
                        "alert_on_high_risk":
                            True,
                        "alert_on_medium_risk":
                            False,
                        "alert_on_pattern":
                            True,
                        "alert_on_anomaly":
                            True,
                        "is_active":
                            True,
                    },
                )
            )

            assert (
                same_policy_response.status_code
                == 200
            ), same_policy_response.text

        verification_db = (
            SessionLocal()
        )

        try:
            revision = (
                load_alert_policy_revision(
                    verification_db,
                    organization_id=(
                        organization.id
                    ),
                )
            )

            assert revision is None

        finally:
            verification_db.close()

    finally:
        _clear_overrides()
        db.close()

        _cleanup(
            organization_ids
        )

def test_repeated_watchlist_archive_does_not_create_extra_policy_revision(
    monkeypatch: pytest.MonkeyPatch,
):
    db = SessionLocal()
    organization_ids = []

    try:
        organization = (
            _create_organization(
                db,
                prefix="watchlist-repeat-archive",
            )
        )

        organization_ids.append(
            organization.id
        )

        watchlist = (
            _create_watchlist(
                db,
                organization_id=(
                    organization.id
                ),
                name="Repeated Archive",
            )
        )

        watchlist_id = (
            watchlist.id
        )

        db.commit()

        _allow_auth_middleware(
            monkeypatch
        )
        _override_authenticated_user(
            organization.id
        )

        with TestClient(
            app
        ) as client:
            headers = (
                _csrf_headers(
                    client
                )
            )

            first_response = (
                client.delete(
                    (
                        "/api/temporal/"
                        "watchlist/"
                        f"{watchlist_id}"
                    ),
                    headers=headers,
                )
            )

            second_response = (
                client.delete(
                    (
                        "/api/temporal/"
                        "watchlist/"
                        f"{watchlist_id}"
                    ),
                    headers=headers,
                )
            )

        assert (
            first_response.status_code
            == 200
        ), first_response.text

        assert (
            second_response.status_code
            == 200
        ), second_response.text

        verification_db = (
            SessionLocal()
        )

        try:
            revision = (
                load_alert_policy_revision(
                    verification_db,
                    organization_id=(
                        organization.id
                    ),
                )
            )

            assert revision is not None
            assert (
                revision.policy_revision
                == 1
            )

        finally:
            verification_db.close()

    finally:
        _clear_overrides()
        db.close()

        _cleanup(
            organization_ids
        )

def test_create_watchlist_rolls_back_when_policy_revision_bump_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    db = SessionLocal()
    organization_ids = []

    try:
        organization = (
            _create_organization(
                db,
                prefix="watchlist-create-rollback",
            )
        )

        organization_ids.append(
            organization.id
        )

        db.commit()

        _allow_auth_middleware(
            monkeypatch
        )
        _override_authenticated_user(
            organization.id
        )

        def _fail_revision_bump(
            *args,
            **kwargs,
        ):
            raise RuntimeError(
                "forced revision failure"
            )

        monkeypatch.setattr(
            temporal_routes,
            "bump_alert_policy_revision",
            _fail_revision_bump,
        )

        with TestClient(
            app
        ) as client:
            with pytest.raises(
                RuntimeError,
                match="forced revision failure",
            ):
                client.post(
                    "/api/temporal/watchlist",
                    headers=_csrf_headers(
                        client
                    ),
                    json={
                        "name":
                            "Must Roll Back",
                        "categories": [
                            "legal",
                        ],
                    },
                )

        verification_db = (
            SessionLocal()
        )

        try:
            watchlists = (
                verification_db.query(
                    Watchlist
                )
                .filter(
                    Watchlist.organization_id
                    == organization.id
                )
                .all()
            )

            revision = (
                load_alert_policy_revision(
                    verification_db,
                    organization_id=(
                        organization.id
                    ),
                )
            )

            assert watchlists == []
            assert revision is None

        finally:
            verification_db.close()

    finally:
        _clear_overrides()
        db.close()

        _cleanup(
            organization_ids
        )


def test_patch_watchlist_rolls_back_when_policy_revision_bump_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    db = SessionLocal()
    organization_ids = []

    try:
        organization = (
            _create_organization(
                db,
                prefix="watchlist-patch-rollback",
            )
        )

        organization_ids.append(
            organization.id
        )

        watchlist = (
            _create_watchlist(
                db,
                organization_id=(
                    organization.id
                ),
                name="Original Policy",
            )
        )

        watchlist_id = (
            watchlist.id
        )

        db.commit()

        _allow_auth_middleware(
            monkeypatch
        )
        _override_authenticated_user(
            organization.id
        )

        def _fail_revision_bump(
            *args,
            **kwargs,
        ):
            raise RuntimeError(
                "forced revision failure"
            )

        monkeypatch.setattr(
            temporal_routes,
            "bump_alert_policy_revision",
            _fail_revision_bump,
        )

        with TestClient(
            app
        ) as client:
            with pytest.raises(
                RuntimeError,
                match="forced revision failure",
            ):
                client.patch(
                    (
                        "/api/temporal/"
                        "watchlist/"
                        f"{watchlist_id}"
                    ),
                    headers=_csrf_headers(
                        client
                    ),
                    json={
                        "categories": [
                            "finance",
                        ],
                        "alert_on_pattern":
                            False,
                    },
                )

        verification_db = (
            SessionLocal()
        )

        try:
            persisted = (
                verification_db.get(
                    Watchlist,
                    watchlist_id,
                )
            )

            assert persisted is not None
            assert (
                persisted.categories
                == [
                    "legal",
                ]
            )
            assert (
                persisted.alert_on_pattern
                is True
            )

            revision = (
                load_alert_policy_revision(
                    verification_db,
                    organization_id=(
                        organization.id
                    ),
                )
            )

            assert revision is None

        finally:
            verification_db.close()

    finally:
        _clear_overrides()
        db.close()

        _cleanup(
            organization_ids
        )
