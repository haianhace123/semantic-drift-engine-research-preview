"""Admin-only response behavior. No database or provider operations."""

from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException, Response

from src.api import auth_routes


@pytest.mark.parametrize("role,active,deleted", [
    ("user", True, None),
    ("viewer", True, None),
    ("admin", False, None),
    ("admin", True, "deactivated"),
])
def test_inventory_route_rejects_unauthorized_identity(role, active, deleted):
    user = SimpleNamespace(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), role=role,
        is_active=active, deleted_at=deleted,
    )
    with pytest.raises(HTTPException) as error:
        auth_routes.get_organization_data_inventory(Response(), user, None)
    assert error.value.status_code == 403


def test_inventory_route_hides_backend_details_and_disables_cache(monkeypatch):
    user = SimpleNamespace(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), role="admin",
        is_active=True, deleted_at=None,
    )
    response = Response()
    def broken(*args):
        raise auth_routes.OrganizationInventoryUnavailable("Private tenant SQL error")
    monkeypatch.setattr(auth_routes, "build_consistent_organization_data_inventory", broken)
    with pytest.raises(HTTPException) as error:
        auth_routes.get_organization_data_inventory(response, user, object())
    assert error.value.status_code == 503
    assert "Private tenant SQL" not in str(error.value.detail)
    assert response.headers["Cache-Control"] == "no-store"


def test_inventory_uses_authenticated_organization_and_existing_admin_dependency(monkeypatch):
    from fastapi.routing import APIRoute
    route = next(
        route for route in auth_routes.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/auth/organization/data-inventory"
    )
    assert route.methods == {"GET"}
    assert any(dep.name == "user" for dep in route.dependant.dependencies)
    user = SimpleNamespace(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), role="admin",
        is_active=True, deleted_at=None,
    )
    observed = []
    def stub(db, user_id, organization_id):
        assert user_id == user.id
        observed.append(organization_id)
        return {"status": "PARTIAL_READ_ONLY_INVENTORY_NOT_EXPORT_OR_ERASURE"}
    monkeypatch.setattr(auth_routes, "build_consistent_organization_data_inventory", stub)
    result = auth_routes.get_organization_data_inventory(Response(), user, object())
    assert observed == [user.organization_id]
    assert result["status"].startswith("PARTIAL_")


def test_two_admin_identities_cannot_choose_another_tenant(monkeypatch):
    seen = []
    def record(db, user_id, organization_id):
        assert isinstance(user_id, uuid.UUID)
        seen.append(organization_id)
        return {"organization_id": str(organization_id), "status": "PARTIAL_"}
    monkeypatch.setattr(auth_routes, "build_consistent_organization_data_inventory", record)
    first, second = uuid.uuid4(), uuid.uuid4()
    for org in (first, second):
        user = SimpleNamespace(
            id=uuid.uuid4(), organization_id=org, role="admin",
            is_active=True, deleted_at=None,
        )
        result = auth_routes.get_organization_data_inventory(Response(), user, object())
        assert result["organization_id"] == str(org)
    assert seen == [first, second]
