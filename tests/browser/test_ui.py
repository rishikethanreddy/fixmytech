"""Browser tests of the real UI (Playwright + installed Edge/Chrome). Deselected by default:

    pytest -m browser tests/browser

The backend runs in-process with the MOCK FlipValue and pricing built from the saved Samsung excerpts; the agent
is the real agent server with replayed ADB output (no phone needed).
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api")

AGENT_TOKEN = "browser-test-agent-token-0123456789"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serve(app: object, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))  # type: ignore[arg-type]
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError("server did not start")


@pytest.fixture(scope="module")
def stack(pricing_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, object]]:
    from fixmytech.agent.server import create_agent_app
    from fixmytech.integrations.flipvalue.client import FlipValueClient, FlipValueSettings
    from fixmytech.integrations.flipvalue.mock_server import create_mock_app
    from fixmytech.server.app import create_app
    from fixmytech.server.config import Settings
    from tests.conftest import FakeAdb, asgi_transport_for

    web_port, agent_port = _free_port(), _free_port()
    db = tmp_path_factory.mktemp("db") / "ui.db"
    settings = Settings(FIXMYTECH_DATABASE_URL=f"sqlite:///{db}", FIXMYTECH_ENV="test", FLIPVALUE_MODE="mock",
                        FIXMYTECH_ALLOW_MOCK_VALUATION=True,
                        FIXMYTECH_PARTNER_CONFIG_PATH=str(tmp_path_factory.mktemp("partner") / "p.json"),
                        FIXMYTECH_PRICING_DIR=str(pricing_dir), FIXMYTECH_AGENT_URL=f"http://127.0.0.1:{agent_port}")
    fv = FlipValueClient(FlipValueSettings(mode="mock", base_url="https://flipvalue.test", api_key="k"),
                         transport=asgi_transport_for(create_mock_app(api_key="k")))
    web = _serve(create_app(settings, flipvalue=fv), web_port)
    agent = _serve(create_agent_app(AGENT_TOKEN, [f"http://127.0.0.1:{web_port}"], adb=FakeAdb(), port=agent_port,
                                    sleeper=lambda _: None), agent_port)
    yield {"base": f"http://127.0.0.1:{web_port}", "agent_port": agent_port}
    web.should_exit = agent.should_exit = True


@pytest.fixture
def page(stack: dict[str, object]) -> Iterator[object]:
    with playwright_api.sync_playwright() as p:
        browser = None
        for channel in ("msedge", "chrome"):
            try:
                browser = p.chromium.launch(channel=channel, headless=True)
                break
            except Exception:  # noqa: BLE001 - try the next installed browser
                continue
        if browser is None:
            pytest.skip("no Edge or Chrome installed")
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.errors = errors  # type: ignore[attr-defined]
        yield pg
        browser.close()
        assert not errors, errors


def test_landing_and_static_pages(page, stack):
    page.goto(stack["base"] + "/")
    page.wait_for_selector("text=How it works")
    for route, text in [("#/privacy", "Never collected"), ("#/support", "Help and troubleshooting"),
                        ("#/settings", "Repair price sources"), ("#/history", "No reports yet"),
                        ("#/does-not-exist", "Page not found")]:
        page.goto(stack["base"] + "/" + route)
        page.wait_for_selector(f"text={text}")


def test_unknown_session_shows_recovery_state(page, stack):
    page.goto(stack["base"] + "/#/s/00000000-0000-4000-8000-000000000000/diagnosis")
    page.wait_for_selector("text=This report isn't available in this browser")


def test_agent_not_running_state(page, stack):
    page.goto(stack["base"] + "/#/settings")
    page.fill("#agent-url", "http://127.0.0.1:9")  # nothing listens there
    page.click("#agent-form button[type=submit]")
    page.goto(stack["base"] + "/#/start?mode=usb")
    page.check('input[name="consent_processing"]')
    page.click("#start-form button[type=submit]")
    page.wait_for_selector("text=Agent not detected")


def test_manual_journey_shows_source_attributed_price(page, stack):
    page.goto(stack["base"] + "/#/start?mode=manual")
    page.fill("#manufacturer", "Samsung")
    page.fill("#model", "SM-S928B")
    page.check('input[name="consent_processing"]')
    page.click("#start-form button[type=submit]")
    page.wait_for_selector("text=Hands-on tests")  # manual journeys start with the tests
    sid = page.url.split("/s/")[1].split("/")[0]
    page.goto(f"{stack['base']}/#/s/{sid}/questions")
    page.wait_for_selector("#q-form")
    page.check('#q-form input[value="battery_drain"]')
    page.click('#q-form button[type="submit"]')
    page.wait_for_selector("text=Question 2")
    # validation: submitting without choosing shows an inline message
    page.click('#q-form button[type="submit"]')
    page.wait_for_selector("text=Choose an answer to continue")
    page.goto(f"{stack['base']}/#/s/{sid}/repair")
    page.wait_for_selector("text=Exact model: Galaxy S24 Ultra (SM-S928B)")
    page.wait_for_selector("text=Before tax")
    page.click("#quote-form button[type=submit]")  # empty amount -> inline validation, no request
    page.wait_for_selector("text=Enter the quoted amount as a number")


def test_usb_scan_and_phone_test_page(page, stack):
    base = stack["base"]
    page.goto(f"{base}/#/pair?agent=http%3A%2F%2F127.0.0.1%3A{stack['agent_port']}&token={AGENT_TOKEN}")
    page.wait_for_selector("text=This browser is paired")
    page.click("text=Start a new USB diagnosis")
    page.check('input[name="consent_processing"]')
    page.click("#start-form button[type=submit]")
    page.click('button[data-action="select"]')
    page.check('#scan-form input[name="consent"]')
    page.click("#scan-form button[type=submit]")
    page.wait_for_selector("text=What the phone reported", timeout=30000)
    sid = page.url.split("/s/")[1].split("/")[0]
    page.goto(f"{base}/#/s/{sid}/tests")
    page.click('button[data-t="link"]')
    path = "/p/" + page.inner_text(".cmd").split("/p/")[1].strip()
    page.goto(base + path)
    page.wait_for_selector("text=Each test takes under a minute")
    page.click('button[data-skip="vibration"]')
    page.wait_for_selector("text=Not run")
    page.goto(base + "/p/" + "x" * 40)
    page.wait_for_selector("text=This test link can't be used")


def test_tests_page_completion_and_question_skipping(page, stack):
    base = stack["base"]
    page.goto(f"{base}/#/start?mode=manual")
    page.check('input[name="consent_processing"]')
    page.click("#start-form button[type=submit]")
    page.wait_for_selector("text=0 of 11 tests done")
    page.click('button[data-manual="buttons"][data-outcome="pass"]')
    page.wait_for_selector("text=1 of 11 tests done")
    # skip everything else: the section is then complete, and the step shows a check mark
    for _ in range(12):
        if "11 of 11" in page.inner_text("#tests-summary"):
            break
        btns = page.query_selector_all("button[data-skip]") or page.query_selector_all(
            "button[data-manual][data-outcome='not_run']")
        btns[0].click()
        page.wait_for_timeout(300)
    page.wait_for_selector("text=All tests done. This section is complete.")
    page.reload()
    page.wait_for_selector('.stepper a.done[href$="/tests"]')


def test_theme_toggle_persists(page, stack):
    page.goto(stack["base"] + "/")
    page.wait_for_selector("text=How it works")
    page.click("#theme-toggle")  # system -> light
    assert page.get_attribute("html", "data-theme") == "light"
    page.click("#theme-toggle")  # light -> dark
    assert page.get_attribute("html", "data-theme") == "dark"
    page.reload()
    page.wait_for_selector("text=How it works")
    assert page.get_attribute("html", "data-theme") == "dark"
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg == "rgb(11, 16, 22)", bg


def test_settings_partner_key_form_and_resale_gate(page, stack):
    base = stack["base"]
    page.goto(f"{base}/#/settings")
    page.wait_for_selector("text=Partner service (FlipValue)")
    page.wait_for_selector("#partner-form")
    assert "Not configured" in page.inner_text("#partner-card")
    assert page.get_attribute("#p-key", "type") == "password"  # the key is never displayed while typing
    page.fill("#p-url", "ftp://bad.example")
    page.fill("#p-key", "some-test-key-123")
    page.click("#partner-form button[type=submit]")
    page.wait_for_selector("text=We couldn't complete that")  # invalid URL is rejected inline
    page.fill("#p-url", "https://flipvalue.test")
    page.click("#partner-form button[type=submit]")
    page.wait_for_selector("text=Saved")
    assert "-123" in page.inner_text("#partner-card")  # only the last four characters are shown
    assert "some-test-key-123" not in page.content()
    page.once("dialog", lambda d: d.accept())
    page.click('[data-partner="remove"]')
    page.wait_for_selector("text=Key removed")


def test_phone_details_only_for_questions_only_mode(page, stack):
    page.goto(stack["base"] + "/#/start?mode=usb")
    page.wait_for_selector("#start-form")
    assert not page.is_visible("#manufacturer") and not page.is_visible("#model")
    assert page.is_visible("#region") and page.is_visible("#usb-note")
    page.check('input[name="mode"][value="manual"]')
    assert page.is_visible("#manufacturer") and page.is_visible("#age_months") and not page.is_visible("#usb-note")
    page.check('input[name="mode"][value="usb"]')
    assert not page.is_visible("#model")
