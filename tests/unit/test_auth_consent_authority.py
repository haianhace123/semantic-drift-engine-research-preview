"""Consent provenance regression without application/DB startup.

The public consent endpoint used to insert unauthenticated, unattributed
consent records. Signup must remain the only user-facing writer here.
"""
import ast
from pathlib import Path

AUTH = Path(__file__).resolve().parents[2] / "src/api/auth_routes.py"


def _tree():
    return ast.parse(AUTH.read_text(encoding="utf-8"))


def _is_router_consent_post(decorator):
    if not isinstance(decorator, ast.Call):
        return False
    call = decorator.func
    return (
        isinstance(call, ast.Attribute)
        and call.attr == "post"
        and isinstance(call.value, ast.Name)
        and call.value.id == "router"
        and bool(decorator.args)
        and isinstance(decorator.args[0], ast.Constant)
        and decorator.args[0].value == "/consent"
    )


def test_unattributed_public_consent_writer_is_not_registered():
    tree = _tree()
    assert not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(_is_router_consent_post(dec) for dec in node.decorator_list)
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.ClassDef) and node.name == "ConsentBody"
        for node in ast.walk(tree)
    )


def test_signup_consent_is_bound_to_identity_and_configured_terms_version():
    tree = _tree()
    signup = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "signup"
    )
    writers = [
        node for node in ast.walk(signup)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ConsentLog"
    ]
    assert len(writers) == 1
    kwargs = {kw.arg: kw.value for kw in writers[0].keywords}
    assert ast.unparse(kwargs["user_id"]) == "user.id"
    assert ast.unparse(kwargs["policy_version"]) == "settings.TERMS_VERSION"
    assert ast.unparse(kwargs["consent_type"]) == "'terms'"
    assert ast.unparse(kwargs["granted"]) == "True"


def test_no_other_consent_writer_is_introduced_in_auth_routes():
    tree = _tree()
    writes = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ConsentLog"
    ]
    assert len(writes) == 1
