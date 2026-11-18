"""Transactions: filing inline with Undo, the checklist keeping up, keyboard shortcuts, bulk actions and search."""
import re

from playwright.sync_api import expect

from slopfi import db, reports

UNCAT = "/transactions?month=2026-09&uncategorised=1"   # the demo's open month: nine still to file


def _label(household, leaf):
    return next(o["label"] for o in reports.category_options(db.connect(household)) if o["label"].endswith(" / " + leaf))


def _rows(page):
    return page.locator("#txn-results tbody tr[id^=txn-]")


def _left(page):
    """Uncategorised count on the sidebar checklist ("9 left")."""
    text = page.locator("#checklist li", has=page.locator('a[href*="uncategorised"]')).locator(".n").inner_text()
    return int(text.split()[0])


def test_file_inline_then_undo(page, household):
    hair = _label(household, "Hair & beauty")
    page.goto(UNCAT)
    rows = _rows(page)
    n = rows.count()
    left = _left(page)
    first = rows.first
    first_id = first.get_attribute("id")
    editor = first.locator("input[list]")                            # uncategorised rows arrive with it open
    editor.fill(hair)
    editor.press("Enter")

    toast = page.locator("#toast .toast").last
    expect(toast).to_contain_text("Filed under Hair & beauty")
    expect(page.locator(f"#{first_id}")).to_have_count(0)          # no longer matches "uncategorised only"
    expect(rows).to_have_count(n - 1)
    expect(page.locator("#checklist")).to_contain_text(f"{left - 1} left")   # the sidebar checklist refreshed

    toast.get_by_role("button", name="Undo").click()
    expect(page.locator("#toast")).to_contain_text("Back to Uncategorised")
    expect(page.locator(f"#{first_id}")).to_be_visible()
    expect(rows).to_have_count(n)
    expect(page.locator("#checklist")).to_contain_text(f"{left} left")


def test_unknown_category_is_refused_in_the_row(page):
    page.goto(UNCAT)
    row = _rows(page).first
    row.locator("input[list]").fill("Not a real category")
    row.locator("input[list]").press("Enter")
    expect(row.locator(".field-error")).to_have_text("Choose a category from the list.")


def test_keyboard_shortcuts(page):
    page.goto("/transactions?month=2026-09")
    rows = _rows(page)
    page.locator("h1").click()                                       # focus the page, not a field
    page.keyboard.press("j")
    expect(rows.nth(0)).to_have_class(re.compile(r"\bis-current\b"))
    page.keyboard.press("j")
    expect(rows.nth(1)).to_have_class(re.compile(r"\bis-current\b"))
    expect(rows.nth(1)).to_be_focused()
    page.keyboard.press("k")
    expect(rows.nth(0)).to_have_class(re.compile(r"\bis-current\b"))

    page.keyboard.press("x")
    expect(rows.nth(0)).to_have_class(re.compile(r"\bis-selected\b"))
    expect(page.locator("#bulk")).to_be_visible()
    page.keyboard.press("x")
    expect(page.locator("#bulk")).to_be_hidden()

    page.keyboard.press("o")
    expect(rows.nth(0)).to_have_class(re.compile(r"\bone-off\b"))
    expect(page.locator("#toast")).to_contain_text("Marked one-off")

    page.keyboard.press("c")                                         # open the editor on a filed row
    expect(rows.nth(0)).to_have_class(re.compile(r"\bis-editing\b"))
    expect(rows.nth(0).locator("input[list]")).to_be_focused()
    page.keyboard.press("Escape")
    expect(rows.nth(0)).not_to_have_class(re.compile(r"\bis-editing\b"))
    expect(rows.nth(0).locator("button.cat")).to_be_focused()

    page.keyboard.press("n")                                         # jumps to the next uncategorised row
    expect(page.locator("tr.is-current.uncat input[list]")).to_be_focused()

    page.locator("h1").click()
    page.keyboard.press("?")
    expect(page.locator("details#keys")).to_have_attribute("open", "")
    page.keyboard.press("Escape")
    expect(page.locator("details#keys")).not_to_have_attribute("open", "")


def test_bulk_file_selected_rows(page, household):
    hair = _label(household, "Hair & beauty")
    page.goto(UNCAT)
    rows = _rows(page)
    n = rows.count()
    rows.nth(0).get_by_label("Select").check()
    rows.nth(1).get_by_label("Select").check()
    bulk = page.locator("#bulk")
    expect(bulk.locator("[data-sel-count]").first).to_have_text("2")
    bulk.get_by_label("Category for the selected transactions").fill(hair)
    bulk.get_by_role("button", name=re.compile("Apply category to 2 selected")).click()
    expect(page.locator("#toast")).to_contain_text("Filed 2 under Hair & beauty")
    expect(rows).to_have_count(n - 2)


def test_search_filters_live_and_lands_in_the_url(page):
    page.goto("/transactions?month=2026-09")
    page.locator("#f-q").fill("OLD BELL")
    expect(page).to_have_url(re.compile(r"q=OLD(%20|\+)BELL"))
    rows = _rows(page)
    expect(rows).to_have_count(3)
    for i in range(3):
        expect(rows.nth(i)).to_contain_text("THE OLD BELL")
