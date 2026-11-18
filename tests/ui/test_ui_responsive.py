"""Narrow screens: no page scrolls sideways, the tablet rail and the phone top bar; and the Settings inline rename."""
import pytest
from playwright.sync_api import expect

from test_ui_shell import PAGES

PHONE, TABLET = {"width": 375, "height": 800}, {"width": 900, "height": 900}


@pytest.mark.parametrize("path", PAGES)
def test_no_sideways_scroll_on_a_phone(page, path):
    page.set_viewport_size(PHONE)
    page.goto(path)
    overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
    assert overflow <= 0, f"{path} is {overflow}px wider than a 375px screen"


def test_tablet_rail_and_checklist_on_the_overview(page):
    page.set_viewport_size(TABLET)
    page.goto("/")
    expect(page.locator("button[data-toggle-sidebar]")).to_be_hidden()
    expect(page.locator("#checklist")).to_be_hidden()
    expect(page.locator("#checklist-m")).to_be_visible()
    link = page.locator('nav[aria-label="Main"] a[href="/spending"]')
    expect(link).to_have_accessible_name("Spending")                 # icon-only, still named


def test_phone_setup_menu_opens_and_closes_on_outside_click(page):
    page.set_viewport_size(PHONE)
    page.goto("/")
    menu = page.locator("details#nav-setup")
    expect(menu).not_to_have_attribute("open", "")
    menu.locator("summary").click()
    expect(menu.get_by_role("link", name="Settings")).to_be_visible()
    page.locator("main h1").click()
    expect(menu).not_to_have_attribute("open", "")
    menu.locator("summary").click()
    menu.get_by_role("link", name="Settings").click(timeout=2000)     # fails if the dropdown is clipped or covered
    expect(page).to_have_url("/settings")


def test_rename_a_person_inline(page):
    page.goto("/settings")
    rename = page.get_by_role("button", name="Rename Sam")
    rename.click()
    field = page.get_by_label("New name for Sam")
    expect(field).to_be_visible()
    field.fill("Samira")
    field.press("Enter")
    expect(page.locator("#toast")).to_contain_text("Samira")
    expect(page.get_by_role("button", name="Rename Samira")).to_be_attached()
