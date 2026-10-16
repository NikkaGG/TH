"""Real browser check of the local API panel, using the configured key only in memory."""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright
from tiktok_api.config import get_settings

evidence = Path(__file__).resolve().parents[1] / "docs/evidence"
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:8001/")
    page.locator("#key").fill("intentionally-wrong-key")
    page.locator("#login-form button").click()
    page.wait_for_function("document.querySelector('#message').textContent.includes('Authentication')")
    page.locator("#key").fill(get_settings().admin_key.get_secret_value())
    page.locator("#login-form button").click()
    page.locator("#workspace").wait_for(state="visible")
    page.locator("#accounts-list .empty").wait_for()
    assert page.locator("#key").input_value() == ""
    page.screenshot(path=str(evidence / "api-panel-desktop.png"), full_page=True)
    for tab in ["campaigns", "drafts", "jobs", "accounts"]:
        page.locator(f'nav button[data-tab="{tab}"]').click()
        assert page.locator(f"#{tab}").is_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(evidence / "api-panel-mobile.png"), full_page=True)
    page.locator("#logout").click()
    assert page.locator("#login").is_visible()
    assert not errors, errors
    browser.close()
    result = {
        "login": "passed",
        "tabs": "passed",
        "logout": "passed",
        "mobile_overflow": False,
        "browser_errors": errors,
        "test_data_seeded": False,
    }
    (evidence / "ui-smoke.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
