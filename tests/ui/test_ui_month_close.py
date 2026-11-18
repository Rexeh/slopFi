"""The month-close ritual end to end: file what's left, update balances, close the month; and accepting a spike."""
import re

from playwright.sync_api import expect

from slopfi import db, reports


def test_close_september_from_the_sidebar_checklist(page, household):
    conn = db.connect(household)
    with_target = set(reports.budgets(conn))
    tops = {c["id"]: c for c in reports.categories_tree(conn)}
    # a spending category with no target above it, so filing into it can't break the Targets step
    label = next(f"{t['name']} / {c['name']}" for t in tops.values() if t["kind"] == "expense" and t["id"] not in with_target
                 for c in t["children"] if c["id"] not in with_target)
    leaf = label.split(" / ")[1]
    page.goto("/review?month=2026-09")
    close = page.get_by_role("button", name="Close September").first
    expect(close).to_be_disabled()

    # 1. categorise: the checklist links to the uncategorised rows; select them all and file in bulk
    page.locator("#step-categorise").get_by_role("link").click()
    expect(page).to_have_url(re.compile(r"/transactions\?.*uncategorised=1"))
    page.locator("#sel-all").check()
    page.get_by_label("Category for the selected transactions").fill(label)
    page.get_by_role("button", name=re.compile(r"Apply category to \d+ selected")).click()
    expect(page.locator("#toast")).to_contain_text(re.compile(rf"Filed \d+ under {re.escape(leaf)}"))
    expect(page.locator("#checklist li", has=page.locator('a[href*="uncategorised"]'))).to_have_class(re.compile(r"\bis-done\b"))
    expect(page.locator("#checklist")).to_contain_text("4 of 5")

    # 2. balances: Update balances opens every editable row; re-enter each shown balance
    page.goto("/networth")
    page.locator("#update-balances").click()
    expect(page.locator("#balances-form")).to_have_class(re.compile(r"\bis-updating\b"))
    inputs = page.locator('#balances-form input[id^="bal_"], #balances-form .editing input:not([type=date])')
    expect(inputs.first).to_be_focused()
    for i in range(inputs.count()):
        box = inputs.nth(i)
        if box.is_visible():
            box.fill(box.get_attribute("placeholder").replace("£", "") or "0")
    page.get_by_role("button", name="Save balances").click()
    expect(page.locator("#toast")).to_contain_text(re.compile(r"\d+ balances? saved"))

    # 3. close: every step done, so the button works
    page.goto("/review?month=2026-09")
    expect(page.locator("li.step.is-todo")).to_have_count(0)
    close = page.get_by_role("button", name="Close September").first
    expect(close).to_be_enabled()
    close.click()
    expect(page.locator("#toast")).to_contain_text("September")
    expect(page.get_by_role("button", name="Reopen September", exact=True)).to_be_visible()


def test_accepting_a_spike_returns_to_its_step(page, household):
    conn = db.connect(household)
    with conn:   # one huge meal out in September makes Eating out a spike
        conn.execute("""UPDATE transactions SET amount = -2500 WHERE id = (
            SELECT t.id FROM transactions t JOIN categories c ON c.id = t.category_id JOIN categories p ON p.id = c.parent_id
            WHERE p.name = 'Eating out' AND t.date LIKE '2026-09%' LIMIT 1)""")
    page.goto("/review?month=2026-09")
    step = page.locator("#step-one_offs")
    expect(step).to_have_class(re.compile(r"\bis-todo\b"))
    step.get_by_role("button", name="Accept Eating out as normal for September").click()
    expect(page).to_have_url(re.compile(r"#step-one_offs$"))
    expect(page.locator("#step-one_offs")).to_have_class(re.compile(r"\bis-done\b"))
    expect(page.locator("#step-one_offs")).to_be_in_viewport()
    expect(page.get_by_role("button", name="Undo accepting Eating out")).to_be_visible()
