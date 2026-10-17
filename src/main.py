"""
FastAPI application entry point for Semantic Drift Engine.
"""

import os
from datetime import datetime

from fastapi import Depends, FastAPI, Request
from src.api.system_routes import router as system_router
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from src.api.auth_routes import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    clear_auth_cookies,
    ensure_csrf_cookie,
)
from src.api.auth_routes import router as auth_router
from src.api.dashboard_routes import router as dashboard_router
from src.api.routes.alerts import router as alerts_router
from src.api.routes.lineage import router as lineage_router
from src.api.routes.operations import router as operations_router
from src.api.routes.sources import router as sources_router
from src.api.temporal_routes import router as temporal_router
from src.core.config import settings
from src.core.database import SessionLocal, engine
from src.core.logging import setup_logging
from src.core.security import (
    AuthError,
    authenticate_access_token,
    get_current_user,
    verify_token,
)
from src.models import Entity
from src.middleware.request_context import RequestContextMiddleware


# ============================================================
# LOGGING
# ============================================================

logger = setup_logging(
    log_level="INFO",
)

# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title="Semantic Drift Engine",
    description=(
        "Temporal Knowledge Integrity control plane for detecting "
        "knowledge change, tracing exact downstream dependencies, "
        "and coordinating targeted invalidation."
    ),
    version="1.0.0",
)


# ============================================================
# ROUTERS
# ============================================================

app.include_router(auth_router)
app.include_router(system_router)

app.include_router(
    dashboard_router,
    dependencies=[Depends(get_current_user)],
)

app.include_router(
    temporal_router,
    dependencies=[Depends(get_current_user)],
)

app.include_router(
    alerts_router,
    dependencies=[Depends(get_current_user)],
)

app.include_router(
    lineage_router
)

app.include_router(
    operations_router
)

app.include_router(
    sources_router,
    dependencies=[Depends(get_current_user)],
)


# ============================================================
# SECURITY HEADERS
# ============================================================


class SecurityHeadersMiddleware:
    def __init__(self, application):
        self.app = application

    async def __call__(
        self,
        scope,
        receive,
        send,
    ):
        if scope["type"] != "http":
            await self.app(
                scope,
                receive,
                send,
            )
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(
                    message.get(
                        "headers",
                        [],
                    )
                )

                additions = {
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"referrer-policy": (
                        b"strict-origin-when-cross-origin"
                    ),
                    b"permissions-policy": (
                        b"camera=(), "
                        b"microphone=(), "
                        b"geolocation=()"
                    ),
                    b"content-security-policy": (
                        b"default-src 'self'; "
                        b"img-src 'self' data:; "
                        # Chronoscope positions timeline points using a
                        # bounded inline style; do not enforce style-src
                        # until those dynamic positions are migrated.
                        b"style-src 'self' 'unsafe-inline'; "
                        b"font-src 'self'; "
                        # Auth/legal pages and Vite assets are all locally
                        # served. No inline JavaScript or third-party CDN.
                        b"script-src 'self'; "
                        b"object-src 'none'; "
                        b"connect-src 'self'; "
                        b"frame-ancestors 'none'; "
                        b"base-uri 'self'; "
                        b"form-action 'self'"
                    ),
                }

                if settings.COOKIE_SECURE:
                    additions[
                        b"strict-transport-security"
                    ] = (
                        b"max-age=31536000; "
                        b"includeSubDomains"
                    )

                existing = {
                    key.lower()
                    for key, _ in headers
                }

                for key, value in additions.items():
                    if key not in existing:
                        headers.append(
                            (
                                key,
                                value,
                            )
                        )

                message["headers"] = headers

            await send(message)

        await self.app(
            scope,
            receive,
            send_with_headers,
        )


# ============================================================
# API AUTH MIDDLEWARE
# ============================================================


class ApiAuthMiddleware:
    PUBLIC_PATHS = (
        "/api/auth",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/health",
    )

    PUBLIC_PREFIXES = (
        "/api/auth/",
        "/docs/",
        "/redoc/",
    )

    API_KEY_PREFIXES = (
        "/api/lineage/",
    )

    def __init__(self, application):
        self.app = application

    async def __call__(
        self,
        scope,
        receive,
        send,
    ):
        if scope["type"] != "http":
            await self.app(
                scope,
                receive,
                send,
            )
            return

        path = scope.get(
            "path",
            "",
        )

        if (
            not path.startswith("/api/")
            or path in self.PUBLIC_PATHS
            or path.startswith(self.PUBLIC_PREFIXES)
        ):
            await self.app(
                scope,
                receive,
                send,
            )
            return

        if path.startswith(
            self.API_KEY_PREFIXES
        ):
            await self.app(
                scope,
                receive,
                send,
            )
            return

        headers = dict(
            scope.get(
                "headers",
                [],
            )
        )

        cookie_header = (
            headers.get(
                b"cookie",
                b"",
            )
            .decode("latin1")
        )

        token = None

        for part in cookie_header.split(";"):
            name, _, value = (
                part.strip().partition("=")
            )

            if name == ACCESS_COOKIE:
                token = value
                break

        if not token:
            authorization = (
                headers.get(
                    b"authorization",
                    b"",
                )
                .decode("latin1")
            )

            if authorization.lower().startswith(
                "bearer "
            ):
                token = authorization[7:]

        if (
            not token
            or verify_token(token) is None
        ):
            response = Response(
                content=(
                    '{"error":{'
                    '"code":"AUTH_004",'
                    '"message":"Invalid token",'
                    '"details":null'
                    "}}"
                ),
                media_type="application/json",
                status_code=401,
            )

            await response(
                scope,
                receive,
                send,
            )
            return

        await self.app(
            scope,
            receive,
            send,
        )


app.add_middleware(ApiAuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)


def _dashboard_index_path() -> str:
    compiled_path = os.path.join(
        os.path.dirname(__file__),
        "static",
        "app",
        "index.html",
    )

    if not os.path.isfile(compiled_path):
        raise RuntimeError(
            "Compiled dashboard entry is missing: "
            f"{compiled_path}"
        )

    return compiled_path


# ============================================================
# PROTECTED DASHBOARD
# ============================================================


@app.get(
    "/dashboard/index.html",
    response_class=HTMLResponse,
)
def dashboard_index(
    request: Request,
):

    token = request.cookies.get(
        ACCESS_COOKIE
    )

    if not token:
        return RedirectResponse(
            url="/login.html",
            status_code=303,
        )

    db = SessionLocal()

    try:
        authenticate_access_token(
            token,
            db,
        )

    except AuthError:
        return RedirectResponse(
            url="/login.html",
            status_code=303,
        )

    finally:
        db.close()

    path = _dashboard_index_path()

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as file:
        html = file.read()

    response = HTMLResponse(html)

    if not request.cookies.get(
        CSRF_COOKIE
    ):
        ensure_csrf_cookie(response)

    return response


@app.get("/dashboard")
@app.get("/dashboard/")
def canonical_dashboard_entry():
    """Direct historical directory URLs to the protected React entrypoint."""
    return RedirectResponse(
        url="/dashboard/index.html",
        status_code=303,
    )


@app.get("/dashboard/pages/settings.html")
def legacy_dashboard_settings_page():
    return RedirectResponse(
        url="/dashboard/index.html#account",
        status_code=303,
    )


# ============================================================
# STATIC FILES
# ============================================================

app.mount(
    "/dashboard",
    StaticFiles(
        directory="src/static",
        html=True,
    ),
    name="dashboard",
)


# ============================================================
# ROOT
# ============================================================


@app.get("/")
def root():
    return RedirectResponse(
        url="/login.html"
    )


@app.get("/login.html")
def login_page():
    path = os.path.join(
        os.path.dirname(__file__),
        "static",
        "login.html",
    )

    response = FileResponse(path)

    # Login is the post-logout landing route. Removing stale auth
    # cookies here prevents an invalid browser session from leaking
    # into the next authentication attempt.
    clear_auth_cookies(response)
    ensure_csrf_cookie(response)

    return response


@app.get("/signup.html")
def signup_page():
    return FileResponse(
        os.path.join(
            os.path.dirname(__file__),
            "static",
            "signup.html",
        )
    )


@app.get("/forgot-password.html")
def forgot_password_page():
    return FileResponse(
        os.path.join(
            os.path.dirname(__file__),
            "static",
            "forgot-password.html",
        )
    )


@app.get("/reset-password.html")
def reset_password_page():
    return FileResponse(
        os.path.join(
            os.path.dirname(__file__),
            "static",
            "reset-password.html",
        )
    )


@app.get("/settings.html")
def settings_page():
    return RedirectResponse(
        url="/dashboard/index.html#account",
        status_code=303,
    )


@app.get("/terms.html")
def terms_page():
    return FileResponse(
        os.path.join(
            os.path.dirname(__file__),
            "static",
            "terms.html",
        )
    )


@app.get("/privacy.html")
def privacy_page():
    return FileResponse(
        os.path.join(
            os.path.dirname(__file__),
            "static",
            "privacy.html",
        )
    )


@app.get("/oauth-link.html")
def oauth_link_page():
    return FileResponse(
        os.path.join(
            os.path.dirname(__file__),
            "static",
            "oauth-link.html",
        )
    )

# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
    )