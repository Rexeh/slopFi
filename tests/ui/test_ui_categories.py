"""Categories and rules: spreading a category, the rule switch, tabs, dialogs, inline rename and confirmed deletes."""
import re

from playwright.sync_api import expect

from slopfi import db


def _id(household, path):
    return db.category_id_by_path(db.connect(household), path)


def _scroll_y(page):
    return page.evaluate("scrollY")


def test_spread_keeps_scroll_and_focus(page, household):
    """The regression: a full reload to /categories#cat-N used to jump the page."""
    fees = _id(household, "Bank charges/Fees")
    page.goto("/categories")
    row = page.locator(f"#cat-{fees}")
    row.scroll_into_view_if_needed()
    page.mouse.wheel(0, -200)                      # leave the row mid-screen, not pinned to an edge
    page.wait_for_timeout(100)
    before = _scroll_y(page)
    assert before > 0
    page.locator(f"#cat-{fees}-spread").click()
    expect(page.locator("#toast")).to_contain_text("Fees is spread over 12 months in trends")
    expect(row).to_contain_text("Spread over 12 months")
    expect(page.locator(f"#cat-{fees}-spread")).to_be_focused()
    assert _scroll_y(page) == before
    assert page.url.endswith("/categories")       # no reload, no anchor


def test_spreading_a_parent_spreads_its_sub_categories(page, household):
    leisure, holidays = _id(household, "Leisure"), _id(household, "Leisure/Holidays")
    page.goto("/categories")
    page.locator(f"#cat-{leisure}-spread").click()
    expect(page.locator(f"#cat-{holidays}")).to_contain_text("Spread over 12 months (from Leisure)")
    expect(page.locator(f"#cat-{holidays}-spread")).to_have_count(0)
    page.locator(f"#cat-{leisure}-spread").click()
    expect(page.locator(f"#cat-{holidays}")).to_contain_text("Month by month")


def test_tabs_follow_the_hash(page):
    page.goto("/categories#rules")
    expect(page.locator("#tab-rules")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#panel-categories")).to_be_hidden()
    page.locator("#tab-categories").click()
    expect(page.locator("#panel-categories")).to_be_visible()
    expect(page).to_have_url(re.compile("#categories$"))
    page.keyboard.press("ArrowRight")
    expect(page.locator("#tab-rules")).to_be_focused()
    expect(page.locator("#panel-rules")).to_be_visible()


def test_rule_switch_swaps_the_row_and_keeps_focus(page, household):
    rule = db.connect(household).execute("SELECT id, pattern FROM rules ORDER BY priority, id LIMIT 1").fetchone()
    page.goto("/categories#rules")
    switch = page.locator(f"#rule-{rule['id']}-switch")
    expect(switch).to_have_attribute("aria-checked", "true")
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "false")
    expect(page.locator(f"#rule-{rule['id']}")).to_contain_text("Disabled")
    expect(page.locator("#toast")).to_contain_text(f"Rule {rule['pattern']} disabled")
    expect(switch).to_be_focused()


def test_add_rename_and_delete_a_category(page):
    page.goto("/categories")
    page.get_by_role("button", name="Add a category").click()
    dialog = page.locator("dialog#add-category")
    expect(dialog).to_be_visible()
    dialog.get_by_label("Name").fill("Llamas")
    dialog.get_by_role("button", name="Add category").click()
    expect(page.locator("#toast")).to_contain_text("Llamas added")
    row = page.locator("tr", has=page.get_by_role("cell", name="Llamas", exact=True))
    expect(row).to_be_visible()

    # inline rename: Esc puts the row back and focus on its Rename button
    rename = page.get_by_role("button", name="Rename Llamas")
    rename.click()
    field = page.get_by_label("New name for Llamas")
    expect(field).to_be_visible()
    field.focus()
    page.keyboard.press("Escape")
    expect(field).to_be_hidden()
    expect(rename).to_be_focused()
    rename.click()
    field.fill("Alpacas")
    field.press("Enter")
    expect(page.locator("#toast")).to_contain_text("Renamed to Alpacas")

    # delete asks first; Cancel keeps it
    delete = page.get_by_role("button", name="Delete Alpacas")
    delete.click()
    confirm = page.locator("dialog#confirm")
    expect(confirm).to_be_visible()
    expect(confirm.locator("#confirm-title")).to_have_text("Delete Alpacas?")
    expect(confirm.get_by_role("button", name="Cancel")).to_be_focused()
    confirm.get_by_role("button", name="Cancel").click()
    expect(confirm).to_be_hidden()
    delete.click()
    confirm.get_by_role("button", name="Delete").click()
    expect(page.locator("#toast")).to_contain_text("Alpacas deleted")
    expect(page.get_by_role("cell", name=re.compile("^Alpacas$"))).to_have_count(0)
