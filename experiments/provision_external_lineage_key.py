from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
AUTH_PREFIX = "/api/auth"
CSRF_HEADER = "X-CSRF-Token"


class ExternalProvisioningError(RuntimeError):
    pass


def _request_json(
    *,
    opener: urllib.request.OpenerDirector,
    url: str,
    method: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = dict(headers or {})
    data: bytes | None = None

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url=url,
        data=data,
        method=method,
        headers=request_headers,
    )

    try:
        with opener.open(
            request,
            timeout=10,
        ) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode(
            "utf-8",
            errors="replace",
        )
        raise ExternalProvisioningError(
            f"HTTP {exc.code} from {url}: {response_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ExternalProvisioningError(
            f"Unable to reach {url}: {exc.reason}"
        ) from exc

    if not response_body:
        return {}

    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise ExternalProvisioningError(
            f"Non-JSON response from {url}: {response_body}"
        ) from exc

    if not isinstance(parsed, dict):
        raise ExternalProvisioningError(
            f"Unexpected response from {url}: {parsed!r}"
        )

    return parsed


def _resolve_csrf_token(
    cookie_jar: http.cookiejar.CookieJar,
) -> str:
    candidates = [
        cookie
        for cookie in cookie_jar
        if cookie.path == "/"
        and cookie.name.endswith("csrf")
    ]

    if not candidates:
        raise ExternalProvisioningError(
            "The CSRF bootstrap did not provide the CSRF cookie."
        )

    if len(candidates) != 1:
        names = sorted(
            cookie.name
            for cookie in candidates
        )
        raise ExternalProvisioningError(
            "Expected exactly one CSRF cookie, "
            f"found: {names}"
        )

    token = candidates[0].value.strip()

    if not token:
        raise ExternalProvisioningError(
            "The CSRF cookie is empty."
        )

    return token


def provision_api_key(
    *,
    base_url: str,
    email: str,
    password: str,
    key_name: str,
) -> str:
    cookie_jar = http.cookiejar.CookieJar()

    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(
            cookie_jar
        )
    )

    auth_url = (
        f"{base_url.rstrip('/')}"
        f"{AUTH_PREFIX}"
    )

    _request_json(
        opener=opener,
        url=f"{auth_url}/csrf",
        method="GET",
    )

    csrf_token = _resolve_csrf_token(
        cookie_jar
    )

    _request_json(
        opener=opener,
        url=f"{auth_url}/login",
        method="POST",
        headers={
            CSRF_HEADER: csrf_token,
        },
        payload={
            "email": email,
            "password": password,
            "remember_me": False,
        },
    )

    csrf_token = _resolve_csrf_token(
        cookie_jar
    )

    result = _request_json(
        opener=opener,
        url=f"{auth_url}/api-keys",
        method="POST",
        headers={
            CSRF_HEADER: csrf_token,
        },
        payload={
            "name": key_name,
            "permissions": {
                "read": True,
                "write": True,
            },
        },
    )

    raw_key = result.get("key")

    if not isinstance(raw_key, str) or not raw_key.strip():
        raise ExternalProvisioningError(
            "API key creation succeeded but the response "
            "did not contain a raw key."
        )

    return raw_key.strip()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Provision a write-capable Semantic Drift Engine "
            "API key through the public authentication API."
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv(
            "SDE_BASE_URL",
            DEFAULT_BASE_URL,
        ),
    )
    parser.add_argument(
        "--email",
        default=os.getenv(
            "SDE_USER_EMAIL",
        ),
    )
    parser.add_argument(
        "--key-name",
        default="phase-7e-external-lineage",
    )

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    email = args.email

    if not email:
        email = input(
            "SDE account email: "
        ).strip()

    if not email:
        print(
            "An SDE account email is required.",
            file=sys.stderr,
        )
        return 2

    password = os.getenv(
        "SDE_USER_PASSWORD"
    )

    if password is None:
        password = getpass.getpass(
            "SDE account password: "
        )

    if not password:
        print(
            "An SDE account password is required.",
            file=sys.stderr,
        )
        return 2

    try:
        raw_key = provision_api_key(
            base_url=args.base_url,
            email=email,
            password=password,
            key_name=args.key_name,
        )
    except ExternalProvisioningError as exc:
        print(
            str(exc),
            file=sys.stderr,
        )
        return 1

    print()
    print(
        "Write-capable SDE API key created successfully."
    )
    print(
        "The raw key is shown once. "
        "Do not commit or share it."
    )
    print()
    print(raw_key)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())