"""A healthy database connection is not enough for production readiness."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import Response

from src.api import system_routes
from src.services.schema_compatibility_service import SchemaCompatibilityError


def _stub_dependencies(monkeypatch):
    engine = MagicMock()
    monkeypatch.setattr(system_routes, "engine", engine)
    redis = MagicMock()
    monkeypatch.setattr(system_routes, "_redis_client", lambda: redis)
    return engine, redis


def test_prod_readiness_refuses_incompatible_schema(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    _stub_dependencies(monkeypatch)
    monkeypatch.setattr(
        system_routes,
        "assert_schema_compatible",
        lambda conn: (_ for _ in ()).throw(SchemaCompatibilityError("unknown ledger")),
    )
    response = Response()
    body = system_routes.readiness(response)
    assert response.status_code == 503
    assert body["status"] == "not_ready"
    assert body["checks"]["database"]["status"] == "schema_incompatible"
    assert body["checks"]["redis"]["status"] == "ok"


def test_prod_readiness_accepts_matching_ledger(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    _stub_dependencies(monkeypatch)
    verified = []
    monkeypatch.setattr(
        system_routes,
        "assert_schema_compatible",
        lambda conn: verified.append(conn),
    )
    response = Response()
    body = system_routes.readiness(response)
    assert response.status_code == 200
    assert body["status"] == "ready"
    assert len(verified) == 1


def test_dev_readiness_does_not_require_prod_schema_contract(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    _stub_dependencies(monkeypatch)
    monkeypatch.setattr(
        system_routes,
        "assert_schema_compatible",
        lambda conn: (_ for _ in ()).throw(AssertionError("unexpected check")),
    )
    response = Response()
    assert system_routes.readiness(response)["status"] == "ready"
