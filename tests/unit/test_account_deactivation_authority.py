"""Identity deactivation protects last-admin custody and audit provenance."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from datetime import datetime, timezone
import ast
import types

import pytest
from fastapi import HTTPException, Response

ROOT = Path(__file__).resolve().parents[2]


class Field:
    def __eq__(self, _other):
        return self

    def __ne__(self, _other):
        return self

    def is_(self, _value):
        return self


class UserModel:
    organization_id = Field()
    id = Field()
    role = Field()
    is_active = Field()
    deleted_at = Field()


class OrganizationModel:
    id = Field()


def load_deactivation_route():
    """Execute the actual route function without importing runtime/database."""
    source = (ROOT / "src/api/auth_routes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    matching = [node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "delete_me"]
    assert len(matching) == 1
    node = matching[0]
    node.decorator_list = []
    env = {
        "Request": object,
        "Response": Response,
        "Session": object,
        "User": UserModel,
        "Organization": OrganizationModel,
        "Depends": lambda dependency: None,
        "get_current_user": lambda: None,
        "get_db": lambda: None,
        "HTTPException": HTTPException,
        "now_utc": lambda: datetime.now(timezone.utc),
    }
    module = types.ModuleType("isolated_account_deactivation")
    module.__dict__.update(env)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / "src/api/auth_routes.py"), "exec"), module.__dict__)
    return module


auth_routes = load_deactivation_route()


class OwnerQuery:
    def __init__(self, org, trace):
        self.org = org
        self.trace = trace

    def filter(self, *args):
        self.trace.append("org_filter")
        return self

    def with_for_update(self):
        self.trace.append("org_row_lock")
        return self

    def one_or_none(self):
        self.trace.append("org_loaded")
        return self.org


class AdminQuery:
    def __init__(self, replacement, trace):
        self.replacement = replacement
        self.trace = trace

    def filter(self, *args):
        self.trace.append("active_admin_filter")
        return self

    def first(self):
        self.trace.append("active_admin_read")
        return self.replacement


class FakeSession:
    def __init__(self, org, replacement, trace):
        self.org = org
        self.replacement = replacement
        self.trace = trace
        self.commit_count = 0

    def query(self, model):
        self.trace.append("query_" + model.__name__)
        if model is auth_routes.Organization:
            return OwnerQuery(self.org, self.trace)
        if model is auth_routes.User:
            return AdminQuery(self.replacement, self.trace)
        raise AssertionError(f"unexpected model: {model}")

    def commit(self):
        self.commit_count += 1
        self.trace.append("commit")


def make_user(role="admin"):
    return SimpleNamespace(
        id=uuid4(), organization_id=uuid4(), role=role,
        is_active=True, deleted_at=None, email="private@example.test",
        full_name="Private User", last_login=object(), email_verified=True,
        login_attempts=5, locked_until=object(), password_hash="hashed",
        google_id="oauth", github_id=None,
        verification_token="a", verification_token_expires=object(),
        verification_code_hash="b", verification_code_expires=object(),
        reset_token="c", reset_token_expires=object(),
        refresh_token="d", refresh_token_expires=object(),
    )


def prepare(monkeypatch, user, org=None, replacement=None):
    trace = []
    db = FakeSession(org, replacement, trace)
    monkeypatch.setattr(auth_routes, "require_csrf", lambda req: trace.append("csrf"), raising=False)
    monkeypatch.setattr(auth_routes, "revoke_all_user_sessions", lambda db_, uid: trace.append("revoke_sessions"), raising=False)
    monkeypatch.setattr(auth_routes, "clear_auth_cookies", lambda response: trace.append("clear_cookies"), raising=False)
    monkeypatch.setattr(auth_routes, "record_auth_security_event", lambda db_, **kwargs: trace.append(("audit", kwargs)), raising=False)
    return db, trace


def invoke(user, db):
    return auth_routes.delete_me(object(), Response(), user, db)


def test_last_admin_refused_without_mutation(monkeypatch):
    user = make_user()
    db, trace = prepare(monkeypatch, user, org=SimpleNamespace(id=user.organization_id))
    with pytest.raises(HTTPException) as exc:
        invoke(user, db)
    assert exc.value.status_code == 409
    assert "last active" in str(exc.value.detail)
    assert user.is_active and user.deleted_at is None
    assert user.email == "private@example.test" and user.password_hash == "hashed"
    assert trace.index("org_row_lock") < trace.index("active_admin_read")
    assert "revoke_sessions" not in trace and "clear_cookies" not in trace
    assert not any(isinstance(v, tuple) and v[0] == "audit" for v in trace)
    assert db.commit_count == 0


def test_admin_with_successor_deactivates_and_audits(monkeypatch):
    user = make_user()
    db, trace = prepare(monkeypatch, user, org=SimpleNamespace(id=user.organization_id), replacement=SimpleNamespace(id=uuid4()))
    result = invoke(user, db)
    assert "deactivated" in result["message"]
    assert user.is_active is False and user.deleted_at is not None
    assert user.email == f"deleted-{user.id}@deleted.invalid"
    assert user.full_name == "" and user.password_hash is None
    for key in ("google_id", "verification_token", "verification_code_hash", "reset_token", "refresh_token"):
        assert getattr(user, key) is None
    assert trace.index("org_row_lock") < trace.index("active_admin_read") < trace.index("revoke_sessions")
    events = [v[1] for v in trace if isinstance(v, tuple) and v[0] == "audit"]
    assert len(events) == 1
    assert events[0]["event_type"] == "account_deactivated"
    assert events[0]["organization_id"] == user.organization_id
    assert events[0]["user_id"] == user.id
    assert events[0]["metadata"]["scope"] == "identity_only"
    assert trace.index("commit") > trace.index("revoke_sessions")
    assert db.commit_count == 1
    assert trace[-1] == "clear_cookies"


def test_missing_organization_fails_closed(monkeypatch):
    user = make_user()
    db, trace = prepare(monkeypatch, user)
    with pytest.raises(HTTPException) as exc:
        invoke(user, db)
    assert exc.value.status_code == 409
    assert user.is_active is True
    assert db.commit_count == 0


def test_non_admin_deactivation_does_not_need_owner_lock(monkeypatch):
    user = make_user("viewer")
    db, trace = prepare(monkeypatch, user)
    invoke(user, db)
    assert "query_Organization" not in trace
    assert user.is_active is False
    assert db.commit_count == 1


def test_deactivation_security_event_type_is_authorized():
    source = (ROOT / "src/services/auth_audit_service.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "frozenset"]
    assert any(
        isinstance(arg, (ast.Set, ast.List, ast.Tuple))
        and "account_deactivated" in [entry.value for entry in arg.elts if isinstance(entry, ast.Constant)]
        for call in calls for arg in call.args
    )
