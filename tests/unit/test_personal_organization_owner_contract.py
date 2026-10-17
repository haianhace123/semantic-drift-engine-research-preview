"""Contracts: creator of a *new* personal organization is its first admin.

Does not grant roles to accounts in existing organizations and does not
substitute for DB-backed authorization and tenant-isolation integration tests.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUTH = ROOT / 'src/api/auth_routes.py'
INTEGRATION = ROOT / 'tests/integration/test_api_key_auth.py'


def _function(name):
    tree = ast.parse(AUTH.read_text(encoding='utf-8'))
    return next(node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)


def _new_owner_call(name):
    node = _function(name)
    calls = [c for c in ast.walk(node) if isinstance(c, ast.Call)]
    personal = [c for c in calls if isinstance(c.func, ast.Name) and c.func.id == 'create_personal_org']
    assert len(personal) == 1, f'{name} must create exactly one personal organization'
    users = [c for c in calls if isinstance(c.func, ast.Name) and c.func.id == 'User']
    assert len(users) == 1, f'{name} should instantiate User only on the new-organization branch'
    users_with_org = [c for c in users if any(k.arg == 'organization_id' and isinstance(k.value, ast.Attribute)
                      and isinstance(k.value.value, ast.Name) and k.value.value.id == 'organization'
                      and k.value.attr == 'id' for k in c.keywords)]
    assert len(users_with_org) == 1
    role = next(k.value for k in users_with_org[0].keywords if k.arg == 'role')
    assert isinstance(role, ast.Constant) and role.value == 'admin', f'{name}: personal org owner must be admin'


def test_email_password_signup_creates_admin_owner():
    _new_owner_call('signup')


def test_oauth_signup_creates_admin_owner():
    _new_owner_call('finish_oauth')


def test_database_integration_contracts_present():
    tree = ast.parse(INTEGRATION.read_text(encoding='utf-8'))
    named = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert {'test_personal_signup_creates_admin_owner',
            'test_new_oauth_personal_organization_creates_admin_owner'} <= named


def test_no_implicit_role_promotion_of_soft_deleted_existing_user():
    # Re-activation is not a reason to gain admin privileges in an existing org.
    signup = _function('signup')
    for branch in ast.walk(signup):
        if not isinstance(branch, ast.If):
            continue
        # Existing deleted account branch assigns ``user = existing``.
        if any(isinstance(n, ast.Assign) and len(n.targets) == 1
               and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'user'
               and isinstance(n.value, ast.Name) and n.value.id == 'existing'
               for statement in branch.body for n in ast.walk(statement)):
            assigned_roles = [n for statement in branch.body for n in ast.walk(statement)
                              if isinstance(n, ast.Attribute) and n.attr == 'role' and isinstance(n.ctx, ast.Store)]
            assert not assigned_roles, 'Restored account role must not be silently escalated'
            return
    raise AssertionError('Existing soft-deleted account path could not be found')
