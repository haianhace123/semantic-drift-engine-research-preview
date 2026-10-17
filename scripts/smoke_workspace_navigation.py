"""Exercise the compiled workspace with browser-local, synthetic API fixtures.

This is a routing/layout regression test. All auth and API responses are
intercepted in the Playwright browser context. No real session is created, no
customer data is read, and this does not establish end-to-end API correctness.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

from scripts.smoke_auth_browser import wait_for_api

EVENT_ID = "b6dc3e58-df27-47ab-9dc5-4a950c281ccd"
DIRECT_EVENT_ID = "a7f82593-c152-49d1-b90c-3aab830b422d"
SOURCE_ID = "d6cbb9e9-e77b-40a9-9e13-83bb31f7a3f7"
ORG_ID = "422a5fa7-6fa2-42eb-88c6-fe4525703787"
ENTITY_ID = "6d690a09-afb2-40c9-a0b1-00ac0454ed3c"

USER = {
    "id": "00000000-0000-4000-8000-000000000001",
    "email": "review@example.invalid",
    "full_name": "Review Operator",
    "role": "viewer",
    "organization_id": ORG_ID,
    "email_verified": True,
    "password_login_enabled": True,
    "google_connected": False,
    "github_connected": False,
    "last_login": None,
}

EVENT = {
    "id": EVENT_ID,
    "organization_id": ORG_ID,
    "entity_id": ENTITY_ID,
    "entity_name": "Public documentation",
    "domain": "example.org",
    "category": "docs",
    "url": "https://example.org/docs",
    "page_type": "docs",
    "monitored_page_id": SOURCE_ID,
    "old_snapshot_id": "00000000-0000-4000-8000-000000000002",
    "new_snapshot_id": "00000000-0000-4000-8000-000000000003",
    "lineage": {"status": "unavailable", "roots": []},
    "summary": "A revision is recorded. Downstream consequences are not established.",
    "risk_level": "unknown",
    "detected_at": "2026-10-09T12:00:00Z",
}

DIRECT_EVENT = {**EVENT, "id": DIRECT_EVENT_ID,
                "summary": "A separately retrieved historical revision."}

SOURCE = {
    "id": SOURCE_ID,
    "entity_id": ENTITY_ID,
    "entity_name": "Public documentation",
    "url": "https://example.org/docs",
    "page_type": "docs",
    "crawl_frequency": "daily",
    "is_active": True,
    "last_crawled_at": "2026-10-09T12:00:00Z",
    "next_crawl_at": "2026-10-10T12:00:00Z",
    "snapshot_count": 2,
    "last_crawl_attempt_at": "2026-10-09T12:00:00Z",
    "last_crawl_status": "succeeded",
    "last_crawl_failure_code": None,
    "consecutive_crawl_failures": 0,
}

DIAGNOSTICS = {
    "source_id": SOURCE_ID,
    "latest_snapshot": {
        "id": EVENT["new_snapshot_id"],
        "crawled_at": "2026-10-09T12:00:00Z",
        "embedding_status": "pending",
    },
    "latest_comparison": {
        "old_snapshot_id": EVENT["old_snapshot_id"],
        "new_snapshot_id": EVENT["new_snapshot_id"],
        "content_changed": True,
        "event_id": EVENT_ID,
        "analysis_status": "pending",
        "vector_analysis_status": "pending",
    },
}


def respond_json(route, payload, status=200):
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(payload),
    )


def install_fixtures(page, html: str) -> None:
    # Register broad fallback first: Playwright evaluates handlers in LIFO order.
    page.route(re.compile(r"/api/"), lambda route: respond_json(
        route, {"detail": "Not available in this synthetic fixture"}, 404,
    ))
    page.route(re.compile(r"/api/temporal/events(?:\?.*)?$"),
               lambda route: respond_json(route, {
                   "total": 1, "offset": 0, "limit": 500, "data": [EVENT],
               }))
    page.route(re.compile(rf"/api/temporal/events/{DIRECT_EVENT_ID}$"),
               lambda route: respond_json(route, DIRECT_EVENT))
    page.route(re.compile(r"/api/sources(?:\?.*)?$"),
               lambda route: respond_json(route, {
                   "total": 1, "offset": 0, "limit": 25, "items": [SOURCE],
               }))
    page.route(re.compile(rf"/api/sources/{SOURCE_ID}/diagnostics(?:\?.*)?$"),
               lambda route: respond_json(route, DIAGNOSTICS))
    page.route(re.compile(r"/api/auth/me(?:\?.*)?$"),
               lambda route: respond_json(route, USER))
    page.route(re.compile(r"/dashboard/index\.html(?:\?.*)?$"),
               lambda route: route.fulfill(status=200, content_type="text/html", body=html))


def run_case(browser, base_url: str, label: str, width: int, height: int,
             output_dir: Path) -> dict:
    context = browser.new_context(
        viewport={"width": width, "height": height},
        reduced_motion="reduce",
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    index_url = base_url.rstrip("/") + "/dashboard/index.html"
    index_path = Path("src/static/app/index.html")
    if not index_path.exists():
        raise FileNotFoundError("Compiled React workspace was not found in the API image")

    install_fixtures(page, index_path.read_text(encoding="utf-8"))
    result: dict = {"viewport": label, "errors": errors}
    try:
        page.goto(index_url, wait_until="domcontentloaded", timeout=20000)
        nav = page.get_by_role("navigation", name="Workspace views")
        nav.wait_for(timeout=15000)
        # The real product mark, not an ornamental CSS stand-in, must load
        # from the same local asset on desktop and mobile.
        brand = page.locator(".workspace-identity img.workspace-mark")
        brand.wait_for()
        assert brand.get_attribute("src") == "/dashboard/assets/brand/logo.svg"
        assert brand.evaluate("el => el.complete && el.naturalWidth > 0"), (
            "Workspace logo did not load"
        )
        mark_box = brand.bounding_box()
        assert mark_box and 36 <= mark_box["width"] <= 48, (
            "Workspace logo is not sized"
        )
        assert 36 <= mark_box["height"] <= 48, (
            "Workspace logo height is not sized"
        )
        favicon = page.locator('head link[rel="icon"][type="image/svg+xml"]')
        assert favicon.get_attribute("href") == "/dashboard/assets/brand/logo.svg", (
            "Compiled workspace has no canonical SVG favicon"
        )
        assert page.get_by_role("heading", name="Change investigation").count() == 1
        # Visual regression: summary was previously an unstyled 10-row DL
        # despite the navigation smoke test passing.
        summary_grid = page.locator(".delivery-summary-primary")
        summary_grid.wait_for()
        assert summary_grid.evaluate(
            "el => getComputedStyle(el).display"
        ) == "grid", "Delivery health summary lacks its grid styles"
        metrics = summary_grid.locator(":scope > div")
        assert metrics.count() == 6, "Delivery health metrics changed unexpectedly"
        first_metric = metrics.nth(0).bounding_box()
        second_metric = metrics.nth(1).bounding_box()
        assert first_metric and second_metric
        assert second_metric["x"] > first_metric["x"] + 4, (
            "Delivery health metrics collapsed to an unstyled vertical list"
        )
        assert page.locator(".delivery-control-plane").evaluate(
            "el => getComputedStyle(el).display"
        ) == "grid", "Delivery ledger and inspector layout styles are absent"

        investigation_screenshot = f"workspace-investigation-{label}.png"
        page.screenshot(
            path=str(output_dir / investigation_screenshot), full_page=True,
            animations="disabled",
        )

        # The account trigger and SVG icon must have bounded dimensions.
        # A missing stylesheet used to render a 200px icon and overlap role text.
        trigger = page.locator(".account-trigger")
        trigger.wait_for()
        trigger_box = trigger.bounding_box()
        chevron_box = page.locator(".account-chevron").bounding_box()
        avatar_box = page.locator(".account-avatar").bounding_box()
        assert trigger_box is not None and trigger_box["height"] <= 60, "Account trigger is oversized"
        assert trigger_box["width"] <= 260, "Account trigger is too wide"
        assert chevron_box is not None and 12 <= chevron_box["width"] <= 20, "Account chevron is not sized"
        assert chevron_box["height"] <= 20, "Account chevron is oversized"
        assert avatar_box is not None and avatar_box["width"] <= 40, "Account avatar is oversized"
        assert trigger_box["x"] >= 0 and trigger_box["x"] + trigger_box["width"] <= width + 1, "Account trigger overflows viewport"

        trigger.click()
        menu = page.locator(".account-popover")
        menu.wait_for()
        assert menu.get_by_role("button", name="Sign out").is_visible()
        popover_box = page.locator(".account-popover").bounding_box()
        assert popover_box is not None and popover_box["x"] >= 0, "Account menu leaves viewport"
        assert popover_box["x"] + popover_box["width"] <= width + 1, "Account menu overflows viewport"
        page.keyboard.press("Escape")
        assert menu.count() == 0, "Escape did not close account menu"

        nav.get_by_role("link", name="Sources").click()
        page.get_by_role("heading", name="Source monitoring").wait_for()
        page.get_by_role("button", name="Inspect pipeline evidence").click()
        page.get_by_role("button", name="Open recorded event").click()
        page.wait_for_url(re.compile(r"#event/" + EVENT_ID + r"$"))
        assert page.get_by_role("heading", name="Event not in loaded history").count() == 0

        nav.get_by_role("link", name="Account").click()
        page.get_by_role("heading", name="Account & security").wait_for()
        assert page.locator(".account-security-layout").evaluate(
            "el => getComputedStyle(el).display"
        ) == "grid", "Account content layout styles are absent"
        index_links = page.locator(".account-security-index a")
        assert index_links.count() >= 4
        first_link = index_links.nth(0).bounding_box()
        next_link = index_links.nth(1).bounding_box()
        assert first_link and next_link
        separated = (
            next_link["x"] >= first_link["x"] + first_link["width"] - 2
            or next_link["y"] >= first_link["y"] + first_link["height"] - 2
        )
        assert separated, "Account section links overlap or run together"
        close_icon = page.locator(".account-security-close svg").bounding_box()
        assert close_icon and 12 <= close_icon["width"] <= 24, (
            "Account close icon has no bounded dimensions"
        )
        password_form = page.locator(".account-password-form")
        assert password_form.evaluate(
            "el => getComputedStyle(el).display"
        ) == "grid", "Account password form is not laid out as a grid"
        password_inputs = password_form.locator("input")
        boxes = [password_inputs.nth(i).bounding_box() for i in range(3)]
        assert all(box and box["width"] >= 130 for box in boxes), (
            "Password fields are too narrow"
        )
        if width <= 768:
            assert boxes[0]["y"] < boxes[1]["y"] < boxes[2]["y"], (
                "Mobile password fields do not stack vertically"
            )
        account_screenshot = f"workspace-account-{label}.png"
        page.screenshot(
            path=str(output_dir / account_screenshot), full_page=True,
            animations="disabled",
        )
        page.get_by_role("link", name="Sessions", exact=True).click()
        page.wait_for_url(re.compile(r"#account-sessions$"))
        assert page.get_by_role("heading", name="Account & security").is_visible()

        page.go_back()
        page.wait_for_url(re.compile(r"#account$"))
        page.go_back()
        page.wait_for_url(re.compile(r"#event/" + EVENT_ID + r"$"))
        page.get_by_role("heading", name="Change investigation").wait_for()

        page.goto(index_url + f"#event/{DIRECT_EVENT_ID}", wait_until="domcontentloaded")
        page.get_by_role("heading", name="Exact event outside the loaded history window").wait_for()
        assert page.get_by_role("heading", name="Change investigation").is_visible()

        page.goto(index_url + "#event/not-loaded", wait_until="domcontentloaded")
        page.get_by_role("heading", name="Event unavailable").wait_for()
        assert page.get_by_text("does not exist or is not accessible", exact=False).count() == 1
        page.get_by_role("button", name="Return to loaded change index").click()
        page.wait_for_url(re.compile(r"#integrity$"))

        page.goto(index_url + "#sources", wait_until="domcontentloaded")
        page.get_by_role("heading", name="Source monitoring").wait_for()
        assert page.get_by_text("1 monitored source", exact=True).is_visible()
        assert page.get_by_role("button", name="Return to integrity").count() == 0
        overflow = page.evaluate(
            "Math.max(0, document.documentElement.scrollWidth - window.innerWidth)"
        )
        result["horizontal_overflow_px"] = overflow
        if overflow > 2:
            errors.append(f"Horizontal overflow: {overflow}px")
        screenshot = f"workspace-sources-{label}.png"
        page.screenshot(
            path=str(output_dir / screenshot), full_page=True, animations="disabled",
        )
        result["screenshot"] = screenshot
        result["screenshots"] = [investigation_screenshot, account_screenshot, screenshot]
        result["account_trigger_px"] = {
            "width": round(trigger_box["width"], 1),
            "height": round(trigger_box["height"], 1),
            "chevron": round(chevron_box["width"], 1),
        }
    except Exception as error:
        errors.append(f"{type(error).__name__}: {error}")
    finally:
        context.close()
    result["passed"] = not errors
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", default="/tmp/sde-workspace-smoke")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    wait_for_api(args.base_url)

    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for label, width, height in (
                ("desktop", 1440, 900), ("mobile", 390, 844),
            ):
                result = run_case(browser, args.base_url, label, width, height, output_dir)
                results.append(result)
                print(f"{'PASS' if result['passed'] else 'FAIL'} workspace {label}: "
                      f"navigation / event links / account anchors / history")
        finally:
            browser.close()

    (output_dir / "summary.json").write_text(
        json.dumps({"mode": "synthetic_browser_fixture", "results": results}, indent=2),
        encoding="utf-8",
    )
    return 0 if all(row["passed"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
