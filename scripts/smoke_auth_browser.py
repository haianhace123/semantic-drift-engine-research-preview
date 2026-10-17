"""Unauthenticated auth-page browser acceptance with viewport screenshots.

This is a layout/asset/JavaScript smoke test, not an authenticated OAuth or
password-reset test. It uses no credentials and does not mutate account data.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


PAGES = (
    "/login.html",
    "/signup.html",
    "/forgot-password.html",
    "/reset-password.html",
    "/oauth-link.html",
)
VIEWPORTS = (
    ("desktop", 1440, 900),
    ("mobile", 390, 844),
)
ASSET_PREFIXES = (
    "/dashboard/css/",
    "/dashboard/js/",
    "/dashboard/assets/",
)


def wait_for_api(base_url: str, *, timeout_seconds: float = 45.0) -> None:
    """Check the health endpoint before launching browser acceptance.

    Compose `up -d` only means that a container has started, not that its
    application is listening yet. Fail explicitly if startup never completes.
    """
    health_url = base_url.rstrip("/") + "/health"
    deadline = time.monotonic() + timeout_seconds
    last_error = "no response"
    while True:
        try:
            with urlopen(health_url, timeout=2.0) as response:
                if response.status == 200:
                    return
                last_error = f"HTTP {response.status}"
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"API readiness failed for {health_url} "
                f"after {timeout_seconds:g}s; last result: {last_error}"
            )
        time.sleep(min(0.5, remaining))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", default="/tmp/sde-auth-smoke")
    parser.add_argument("--readiness-timeout-seconds", type=float, default=45.0)
    args = parser.parse_args()
    if args.readiness_timeout_seconds <= 0:
        parser.error("--readiness-timeout-seconds must be positive")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # Do not leave screenshots from a prior successful run if this run fails.
    for route in PAGES:
        for label, _, _ in VIEWPORTS:
            (output_dir / f"{route.strip('/').replace('.html', '')}-{label}.png").unlink(
                missing_ok=True
            )

    try:
        wait_for_api(args.base_url, timeout_seconds=args.readiness_timeout_seconds)
    except RuntimeError as exc:
        message = str(exc)
        (output_dir / "summary.json").write_text(
            json.dumps([{"stage": "api_readiness", "errors": [message]}], indent=2),
            encoding="utf-8",
        )
        print(f"FAIL API readiness: {message}")
        return 2

    rows = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for route in PAGES:
                for label, width, height in VIEWPORTS:
                    context = browser.new_context(
                        viewport={"width": width, "height": height},
                        device_scale_factor=1,
                        reduced_motion="reduce",
                    )
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    def record_asset_response(response):
                        path = urlsplit(response.url).path
                        if path.startswith(ASSET_PREFIXES) and response.status >= 400:
                            errors.append(f"Asset {response.status}: {path}")

                    page.on("response", record_asset_response)
                    result = {"page": route, "viewport": label}
                    try:
                        response = page.goto(
                            args.base_url.rstrip("/") + route,
                            wait_until="domcontentloaded",
                            timeout=20000,
                        )
                        page.wait_for_timeout(450)
                        result["http_status"] = response.status if response else None
                        title = page.title()
                        result["title"] = title
                        dimensions = page.evaluate(
                            """() => ({
                                viewport: window.innerWidth,
                                document: document.documentElement.scrollWidth,
                                main: !!document.querySelector('main'),
                                cssLoaded: getComputedStyle(document.body)
                                    .getPropertyValue('--auth-bg').trim().length > 0
                            })"""
                        )
                        result["horizontal_overflow_px"] = max(
                            0, dimensions["document"] - dimensions["viewport"]
                        )
                        if result["http_status"] != 200:
                            errors.append(f"Unexpected document status {result['http_status']}")
                        if not title:
                            errors.append("Missing document title")
                        if not dimensions["main"]:
                            errors.append("Missing main landmark")
                        if not dimensions["cssLoaded"]:
                            errors.append("Auth stylesheet not applied")
                        if result["horizontal_overflow_px"] > 2:
                            errors.append("Horizontal overflow on viewport")
                        icon = page.locator('head link[rel="icon"][type="image/svg+xml"]')
                        if icon.get_attribute("href") != "/dashboard/assets/brand/logo.svg":
                            errors.append("Canonical browser favicon is missing")
                        brand_selector = (
                            ".auth-context .auth-brand-mark img"
                            if label == "desktop"
                            else ".auth-stage .auth-mobile-brand img"
                        )
                        brand = page.locator(brand_selector)
                        if brand.count() != 1 or not brand.is_visible():
                            errors.append(f"Brand mark is missing on {label}")
                        elif brand.get_attribute("src") != "/dashboard/assets/brand/logo.svg":
                            errors.append("Auth brand is not the canonical SVG")
                        elif not brand.evaluate("e => e.complete && e.naturalWidth > 0"):
                            errors.append("Auth brand SVG failed to render")
                        if route == "/oauth-link.html":
                            if not page.locator("#confirmLinkBtn").is_disabled():
                                errors.append("OAuth confirmation enabled without verified link")
                        # Static screenshots alone cannot verify pre-auth
                        # trust language or interactive policy guidance.
                        if route == "/login.html":
                            for claim in ("Secure HttpOnly session", "Protected session"):
                                if page.get_by_text(claim, exact=True).count():
                                    errors.append(f"Unverified pre-auth status claim: {claim}")
                        if route == "/signup.html":
                            feedback = page.locator("#passwordStrength")
                            password = page.locator("#password")
                            initial = feedback.inner_text()
                            if "Very weak" in initial or "12+" not in initial:
                                errors.append(f"Misleading initial password guidance: {initial}")
                            password.fill("abc")
                            pending = feedback.inner_text()
                            if "Still needed:" not in pending:
                                errors.append(f"Missing password-policy feedback: {pending}")
                            password.fill("Aaaaaaaa123!")
                            complete = feedback.inner_text()
                            if complete != "Meets password requirements.":
                                errors.append(f"Incorrect password-policy completion: {complete}")
                            password.fill("")  # Preserve the first-use screenshot.
                        filename = f"{route.strip('/').replace('.html', '')}-{label}.png"
                        page.screenshot(
                            path=str(output_dir / filename),
                            full_page=True,
                            animations="disabled",
                        )
                        result["screenshot"] = filename
                    except Exception as exc:
                        errors.append(f"Browser failure: {type(exc).__name__}: {exc}")
                    finally:
                        result["errors"] = errors
                        rows.append(result)
                        status = "PASS" if not errors else "FAIL"
                        print(f"{status} {route} {label}: {'; '.join(errors) if errors else 'assets/layout/JS'}")
                        context.close()
        finally:
            browser.close()

    (output_dir / "summary.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Screenshots and summary: {output_dir}")
    return 1 if any(row["errors"] for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
