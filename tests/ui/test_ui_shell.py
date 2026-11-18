"""Every page loads without script errors, and the shell (navigation, sidebar) behaves."""
import pytest
from playwright.sync_api import expect

PAGES = {"/": "Overview", "/review": "Review", "/transactions": "Transactions", "/renewals": "Renewals", "/spending": "Spending",
         "/networth": "Net worth", "/projection": "Projection", "/statements": "Accounts and statements",
         "/categories": "Categories and rules", "/settings": "Settings"}


@pytest.mark.parametrize("path", PAGES)
def test_page_loads_and_marks_itself_current(page, path):
    page.goto(path)
    expect(page.locator("h1")).to_be_visible()
    current = page.locator('nav[aria-label="Main"] a[aria-current="page"]')
    expect(current).to_have_count(1)
    assert current.get_attribute("href").split("?")[0] == path


def test_sidebar_collapses_to_a_rail_and_remembers(page):
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto("/")
    toggle = page.locator("button[data-toggle-sidebar]")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    toggle.click()
    expect(page.locator("html")).to_have_attribute("data-sidebar", "rail")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(toggle).to_have_accessible_name("Expand sidebar")
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-sidebar", "rail")
