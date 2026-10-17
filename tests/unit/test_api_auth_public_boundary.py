"""Middleware authorization boundary, without importing the live FastAPI app.

Authorization must never be bypassed by prefix lookalikes such as /api/authz.
The class is extracted from src/main.py without initializing the application,
DB engine, configuration or lifespan.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest


class FakeResponse:
    def __init__(self, *, status_code, **_):
        self.status_code = status_code

    async def __call__(self, _scope, _receive, send):
        await send({"type": "http.response.start", "status": self.status_code, "headers": []})


def _load_middleware():
    main = Path(__file__).resolve().parents[2] / "src/main.py"
    tree = ast.parse(main.read_text(encoding="utf-8-sig"))
    nodes = [n for n in tree.body if isinstance(n, ast.ClassDef)
             and n.name == "ApiAuthMiddleware"]
    assert len(nodes) == 1, "One authoritative ApiAuthMiddleware class is required"
    ns = {"ACCESS_COOKIE": "sde_access", "Response": FakeResponse,
          "verify_token": lambda _: None}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(main), "exec"), ns)
    return ns["ApiAuthMiddleware"]


async def _status(cls, path):
    messages = []

    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def send(message):
        messages.append(message)

    await cls(app)({"type": "http", "path": path, "headers": []}, None, send)
    assert len(messages) == 1
    return messages[0]["status"]


@pytest.mark.parametrize("path", [
    "/api/authz", "/api/authentication-probe", "/api/authorization",
    "/api/auth-old", "/api/authX", "/api/healthcheck", "/api/docs",
    "/api/alerts", "/api/operations",
])
def test_auth_prefix_lookalikes_are_not_public(path):
    assert asyncio.run(_status(_load_middleware(), path)) == 401


@pytest.mark.parametrize("path", [
    "/api/auth", "/api/auth/login", "/api/auth/csrf", "/api/auth/",
    "/docs", "/docs/", "/redoc", "/redoc/", "/openapi.json", "/health",
])
def test_explicit_public_auth_and_documentation_paths_remain_public(path):
    assert asyncio.run(_status(_load_middleware(), path)) == 200


def test_lineage_prefix_delegates_authorization_to_route():
    # This is a delegation contract, not proof that the route validates API keys.
    assert asyncio.run(_status(_load_middleware(), "/api/lineage/external")) == 200
