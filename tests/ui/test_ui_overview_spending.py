"""Overview and Spending: chart/table twins, the fund target, tabs, sub-category rows and the period filter."""
import re

import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize("path,key", [("/", "where"), ("/", "six"), ("/spending", "matrix")])
def test_chart_table_twin(page, path, key):
    page.goto(path)
    toggle = page.locator(f'[data-table-toggle="{key}"]')
    toggle.scroll_into_view_if_needed()
    expect(page.locator(f"#twin-{key}")).to_be_hidden()
    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "true")
    expect(toggle).to_contain_text("Chart")
    expect(page.locator(f"#twin-{key}")).to_be_visible()
    expect(page.locator(f"#twin-{key} table")).to_be_visible()
    expect(page.locator(f"#chart-{key}-wrap")).to_be_hidden()
    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "false")
    expect(page.locator(f"#chart-{key}-wrap")).to_be_visible()


def test_fund_target_reveal_validate_and_save(page):
    page.goto("/")
    page.get_by_role("button", name="Change target").click()
    field = page.locator("#fund-form input[name=target]")
    expect(field).to_be_focused()
    field.fill("lots")
    field.press("Enter")
    expect(page.locator("#err-fund_target")).to_be_visible()                 # re-rendered with the error showing
    expect(page.locator("#fund-form input[name=target]")).to_have_attribute("aria-invalid", "true")
    page.locator("#fund-form input[name=target]").fill("90000")
    page.get_by_role("button", name="Save target").click()
    expect(page.locator("#toast")).to_contain_text("Target saved")
    expect(page.locator("#fund-form")).to_be_hidden()
    expect(page.locator("main")).to_contain_text("£90,000")


def test_spending_tabs_and_old_recurring_link(page):
    page.goto("/recurring")
    expect(page).to_have_url(re.compile(r"/spending#fixed$"))
    expect(page.locator("#tab-fixed")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#panel-fixed")).to_be_visible()
    page.locator("#tab-targets").click()
    expect(page.locator("#panel-targets")).to_be_visible()
    expect(page).to_have_url(re.compile(r"#targets$"))


def test_changing_the_period_keeps_the_tab(page):
    page.goto("/spending#targets")
    page.locator("select[name=months]").select_option("6")
    page.get_by_role("button", name="Apply").click()
    expect(page).to_have_url(re.compile(r"months=6.*#targets$"))
    expect(page.locator("#panel-targets")).to_be_visible()


def test_sub_category_rows_open_and_are_remembered(page):
    page.goto("/spending")
    toggle = page.locator(".toggle-sub").first
    sub = toggle.get_attribute("data-sub")
    rows = page.locator(f'tr[data-sub-of="{sub}"]')
    expect(rows.first).to_be_hidden()
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(rows.first).to_be_visible()
    page.reload()
    expect(page.locator(f'.toggle-sub[data-sub="{sub}"]')).to_have_attribute("aria-expanded", "true")
    expect(page.locator(f'tr[data-sub-of="{sub}"]').first).to_be_visible()
