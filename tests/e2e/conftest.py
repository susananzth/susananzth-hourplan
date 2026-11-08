"""Browser tests against the demo app (run with `make e2e`, not with the normal suite)."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
SHOTS = Path(os.environ.get("SCREENSHOTS_DIR", "capturas"))
EMAIL = "admin@example.test"
PASSWORD = "e2e-clave-larga-123"


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE


@pytest.fixture(scope="session")
def auth_state(browser: Browser) -> dict:
    """Log in once: the login form is rate limited, so every test reuses this session."""
    context = browser.new_context(base_url=BASE)
    page = context.new_page()
    page.goto("/login")
    page.fill("input[name=email]", EMAIL)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(lambda url: "/login" not in url)
    state = context.storage_state()
    context.close()
    return dict(state)


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict, auth_state: dict) -> dict:
    return {
        **browser_context_args,
        "viewport": {"width": 1280, "height": 800},
        "locale": "es-CL",
        "storage_state": auth_state,
    }


@pytest.fixture
def logged(page: Page) -> Page:
    return page


def shot(page: Page, name: str) -> None:
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)
