from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import src.main as main_module
from src.api.auth_routes import ACCESS_COOKIE, CSRF_COOKIE


def _write_index(
    path: Path,
    marker: str,
) -> Path:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        (
            "<!doctype html>"
            "<html>"
            "<body>"
            f"{marker}"
            "</body>"
            "</html>"
        ),
        encoding="utf-8",
    )

    return path


def test_dashboard_index_redirects_unauthenticated_browser(
    monkeypatch,
):
    monkeypatch.setattr(
        main_module,
        "verify_token",
        lambda token: None,
    )

    client = TestClient(
        main_module.app,
        follow_redirects=False,
    )

    try:
        response = client.get(
            "/dashboard/index.html"
        )

        assert response.status_code == 303
        assert (
            response.headers["location"]
            == "/login.html"
        )

    finally:
        client.close()


def test_dashboard_index_serves_compiled_entry_and_bootstraps_csrf(
    monkeypatch,
    tmp_path,
):
    compiled_index = _write_index(
        tmp_path / "app" / "index.html",
        "compiled-react-entry",
    )

    monkeypatch.setattr(
        main_module,
        "authenticate_access_token",
        lambda token, db: object(),
    )

    dashboard_path_calls = []

    def dashboard_index_path():
        dashboard_path_calls.append(True)
        return str(compiled_index)

    monkeypatch.setattr(
        main_module,
        "_dashboard_index_path",
        dashboard_index_path,
    )

    client = TestClient(
        main_module.app
    )

    try:
        client.cookies.set(
            ACCESS_COOKIE,
            "valid-access-token",
        )

        response = client.get(
            "/dashboard/index.html"
        )

        assert response.status_code == 200
        assert (
            "compiled-react-entry"
            in response.text
        )
        assert dashboard_path_calls == [True]
        assert client.cookies.get(
            CSRF_COOKIE
        )

    finally:
        client.close()


def test_dashboard_index_preserves_existing_csrf_cookie(
    monkeypatch,
    tmp_path,
):
    compiled_index = _write_index(
        tmp_path / "app" / "index.html",
        "compiled-react-entry",
    )

    monkeypatch.setattr(
        main_module,
        "authenticate_access_token",
        lambda token, db: object(),
    )

    monkeypatch.setattr(
        main_module,
        "_dashboard_index_path",
        lambda: str(compiled_index),
    )

    client = TestClient(
        main_module.app
    )

    try:
        client.cookies.set(
            ACCESS_COOKIE,
            "valid-access-token",
        )

        client.cookies.set(
            CSRF_COOKIE,
            "existing-csrf-token",
        )

        response = client.get(
            "/dashboard/index.html"
        )

        assert response.status_code == 200

        assert (
            client.cookies.get(
                CSRF_COOKIE
            )
            == "existing-csrf-token"
        )

    finally:
        client.close()


def test_dashboard_index_path_requires_compiled_workspace(
    monkeypatch,
):
    expected = str(
        Path(main_module.__file__).parent
        / "static"
        / "app"
        / "index.html"
    )

    monkeypatch.setattr(
        main_module.os.path,
        "isfile",
        lambda path: path == expected,
    )

    assert (
        main_module._dashboard_index_path()
        == expected
    )


def test_dashboard_index_path_rejects_missing_compiled_workspace(
    monkeypatch,
):
    monkeypatch.setattr(
        main_module.os.path,
        "isfile",
        lambda path: False,
    )

    try:
        main_module._dashboard_index_path()
    except RuntimeError as exc:
        assert (
            "Compiled dashboard entry is missing"
            in str(exc)
        )
    else:
        raise AssertionError(
            "Missing compiled dashboard must fail closed."
        )


def test_dashboard_index_redirects_rejected_access_token(
    monkeypatch,
):
    def reject_access_token(
        token,
        db,
    ):
        raise main_module.AuthError(
            "AUTH_004",
            "Invalid token",
            401,
        )

    monkeypatch.setattr(
        main_module,
        "authenticate_access_token",
        reject_access_token,
    )

    client = TestClient(
        main_module.app,
        follow_redirects=False,
    )

    try:
        client.cookies.set(
            ACCESS_COOKIE,
            "rejected-access-token",
        )

        response = client.get(
            "/dashboard/index.html"
        )

        assert response.status_code == 303
        assert (
            response.headers["location"]
            == "/login.html"
        )

    finally:
        client.close()


def test_legacy_dashboard_directory_routes_redirect_to_protected_entry():
    """The old static HTML workspace must never be the dashboard default."""
    with TestClient(main_module.app, follow_redirects=False) as client:
        for path in ("/dashboard", "/dashboard/"):
            response = client.get(path)
            assert response.status_code == 303
            assert response.headers["location"] == "/dashboard/index.html"

        response = client.get("/dashboard/index.html")
        assert response.status_code == 303
        assert response.headers["location"] == "/login.html"


def test_obsolete_dashboard_resources_are_not_served():
    with TestClient(main_module.app, follow_redirects=False) as client:
        for path in (
            "/dashboard/js/temporal-workspace.js",
            "/dashboard/js/auth-client.js",
            "/dashboard/css/temporal.css",
            "/dashboard/pages/settings.html.bak",
        ):
            assert client.get(path).status_code == 404

        assert client.get("/dashboard/css/auth.css").status_code == 200
