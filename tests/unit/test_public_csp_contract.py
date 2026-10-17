"""HTTP response security checks for production FastAPI routes."""
from fastapi.testclient import TestClient
from src.main import app

def test_csp_enforced_for_public_html_and_local_assets():
    with TestClient(app) as client:
        for route in ("/login.html", "/terms.html", "/privacy.html"):
            response = client.get(route)
            assert response.status_code == 200, route
            directives = dict(
                part.strip().split(" ", 1)
                for part in response.headers["content-security-policy"].split(";")
                if part.strip() and " " in part.strip()
            )
            assert directives["script-src"] == "'self'", route
            assert directives["object-src"] == "'none'", route
            assert "https:" not in directives["img-src"], route
            assert "fonts.googleapis.com" not in response.headers["content-security-policy"]
            assert "cdnjs.cloudflare.com" not in response.headers["content-security-policy"]
        for asset in ("/dashboard/css/legal.css", "/dashboard/js/legal-pages.js"):
            response = client.get(asset)
            assert response.status_code == 200, asset
            assert response.headers["x-content-type-options"] == "nosniff"


def test_public_auth_exception_does_not_bypass_unrelated_api_routes():
    with TestClient(app) as client:
        for path in ("/api/authentication-probe", "/api/healthcheck", "/api/authz"):
            response = client.get(path)
            assert response.status_code == 401, path
        assert client.get("/health").status_code == 200
