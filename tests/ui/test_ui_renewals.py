"""Renewals end to end: the demo's renewals, adding one, putting it off at month close, and renewing with quotes."""
import re
from datetime import date, timedelta

from playwright.sync_api import expect

TODAY = date(2026, 11, 18)   # the app's pinned today: conftest.TODAY


def test_demo_household_has_renewals_and_suggestions(page):
    page.goto("/renewals")
    expect(page.get_by_role("heading", name="Renewals", level=1)).to_be_visible()
    table = page.locator(".renewals-table")
    for name in ("Car insurance", "Broadband", "Energy fixed tariff", "MOT", "Mortgage fix"):
        expect(table).to_contain_text(name)
    expect(table).to_contain_text("From Net worth")
    expect(page.locator(".suggestions").first).to_contain_text("Homesafe Insurance")


def test_add_defer_and_renew(page):
    ends = (TODAY + timedelta(days=30)).isoformat()
    page.goto("/renewals?add=1#renewal-form")
    page.get_by_label("Name").fill("Breakdown cover")
    page.get_by_label("Provider").fill("Roadready")
    page.get_by_label("Ends").fill(ends)
    page.locator("#f-price").fill("96")
    page.locator("#f-price_period").select_option("year")
    page.get_by_role("button", name="Add renewal").click()
    expect(page.locator("#toast")).to_contain_text("Breakdown cover added")
    due = page.locator("tbody.renewal", has_text="Breakdown cover")
    expect(due.locator(".badge")).to_contain_text("in 30 days")

    # month close: the step lists it; putting it off ticks it for this month only
    page.goto("/review")
    step = page.locator("#step-renewals")
    item = step.locator("li", has_text="Breakdown cover")
    item.get_by_role("button", name="Look at Breakdown cover at the next close").click()
    expect(page.locator("#toast")).to_contain_text("Breakdown cover moved to next close")
    expect(page.locator("#step-renewals li", has_text="Breakdown cover")).to_contain_text("Next close")

    # renew with quotes: the result line works the saving out as you type
    page.goto("/renewals")
    page.locator("tbody.renewal", has_text="Breakdown cover").get_by_role("link", name="Renew Breakdown cover").click()
    page.locator("#f-quote").fill("140")
    page.locator("#f-other_quote").fill("88")
    page.locator("#f-other_provider").fill("Highway Help")
    page.get_by_text("Switched", exact=True).click()
    expect(page.locator("#f-new_provider")).to_have_value("Highway Help")
    expect(page.locator("#f-new_price")).to_have_value("88")
    expect(page.locator("#renew-result")).to_contain_text("£52 a year less than the renewal quote")
    page.get_by_role("button", name="Save renewal").click()
    expect(page.locator("#toast")).to_contain_text("Breakdown cover renewed until")
    row = page.locator("tbody.renewal", has_text="Breakdown cover")
    expect(row).to_contain_text("saved £52")
    expect(row).to_contain_text("Highway Help")


# ------------------------------------------------------------------ more screens
def _badge(page) -> int:
    count = page.locator('nav[aria-label="Main"] a[href="/renewals"] .count')
    return int(count.inner_text()) if count.count() else 0


def test_review_step_defers_and_undoes_with_the_sidebar_count(page):
    page.goto("/review")
    step = page.locator("#step-renewals")
    expect(step).to_contain_text("Car insurance")
    before = _badge(page)
    assert before >= 1
    step.get_by_role("button", name="Look at Car insurance at the next close").click()
    expect(page.locator("#toast")).to_contain_text("Car insurance moved to next close")
    expect(page.locator("#step-renewals li", has_text="Car insurance")).to_contain_text("Next close")
    assert _badge(page) == before - 1
    page.locator("#step-renewals").get_by_role("button", name="Undo putting off Car insurance").click()
    expect(page.locator("#toast")).to_contain_text("Undone: Car insurance is back on this month's list")
    assert _badge(page) == before


def test_defer_from_the_renew_form_returns_to_renewals(page):
    page.goto("/renewals")
    before = _badge(page)
    page.locator("tbody.renewal", has_text="Car insurance").get_by_role("link", name="Renew Car insurance").click()
    page.get_by_role("button", name="Look at it next close").click()
    expect(page.locator("#toast")).to_contain_text("Car insurance moved to next close")
    expect(page).to_have_url(re.compile(r"/renewals#r-\d+$"))
    assert _badge(page) == before - 1


def test_add_from_a_suggestion_then_dismiss_another(page):
    page.goto("/renewals")
    suggestions = page.locator(".suggestions").first
    suggestions.locator("li", has_text="Homesafe Insurance").get_by_role("link", name="Add").click()
    expect(page.locator("#f-provider")).to_have_value("Homesafe Insurance")
    expect(page.locator("#f-price")).to_have_value("23.75")
    expect(page.locator("#f-kind")).to_have_value("insurance")
    expect(page.locator("#f-payee_key")).to_have_value("HOMESAFE INSURANCE")
    page.locator("#f-name").fill("Home insurance")
    page.locator("#f-ends_on").fill((TODAY + timedelta(days=200)).isoformat())
    page.get_by_role("button", name="Add renewal").click()
    expect(page.locator("#toast")).to_contain_text("Home insurance added")
    expect(page.locator("tbody.renewal", has_text="Home insurance")).to_contain_text("direct debit")
    expect(page.locator(".suggestions").first).not_to_contain_text("Homesafe Insurance")

    page.locator(".suggestions").first.get_by_role("button", name=re.compile("Don't suggest Pawsure")).click()
    expect(page.locator("#toast")).to_contain_text("won't be suggested again")
    expect(page.locator(".suggestions").first).not_to_contain_text("Pawsure")


def test_edit_in_months_then_delete_with_confirmation(page):
    page.goto("/renewals")
    page.locator("tbody.renewal", has_text="Broadband").get_by_role("link", name="Edit Broadband").click()
    expect(page.get_by_role("heading", name="Edit Broadband")).to_be_visible()
    page.locator("#f-lead_months").fill("2")
    page.locator("#f-notes").fill("24-month term")
    page.get_by_role("button", name="Save changes").click()
    expect(page.locator("#toast")).to_contain_text("Broadband saved")
    expect(page.locator("tbody.renewal", has_text="Broadband")).to_contain_text("24-month term")

    page.locator("tbody.renewal", has_text="Broadband").get_by_role("link", name="Edit Broadband").click()
    expect(page.locator("#f-lead_months")).to_have_value("2")
    page.get_by_role("button", name="Delete").click()
    confirm = page.locator("dialog#confirm")
    expect(confirm.locator("#confirm-title")).to_have_text("Delete Broadband?")
    confirm.get_by_role("button", name="Cancel").click()
    expect(confirm).to_be_hidden()
    page.get_by_role("button", name="Delete").click()
    confirm.get_by_role("button", name="Delete").click()
    expect(page.locator("#toast")).to_contain_text("Broadband deleted")
    expect(page.locator("tbody.renewal", has_text="Broadband")).to_have_count(0)


def test_car_date_done_moves_the_next_due_date(page):
    page.goto("/renewals")
    page.locator("tbody.renewal", has_text="Exampletown Garage").get_by_role("link", name="Done MOT").click()
    expect(page.locator("#f-quote")).to_have_count(0)      # a car date has no quotes
    expect(page.locator("#f-new_price")).to_have_value("54.85")
    next_due = TODAY + timedelta(days=366)
    page.locator("#f-new_ends_on").fill(next_due.isoformat())
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#toast")).to_contain_text("MOT done; next due")
    row = page.locator("tbody.renewal", has_text="Exampletown Garage")
    expect(row).to_contain_text(f"{next_due.day} {next_due.strftime('%b')} {next_due.year}")


def test_mortgage_renewal_updates_net_worth_and_undo_restores_it(page):
    page.goto("/renewals")
    page.locator("tbody.renewal", has_text="Mortgage fix").get_by_role("link", name="Renew Mortgage fix").click()
    expect(page.locator("#f-new_price")).to_have_count(0)  # the mortgage's price is its monthly payment
    page.locator("#f-quote").fill("1290")
    page.locator("#f-quote_period").select_option("month")
    page.get_by_text("Switched", exact=True).click()
    page.locator("#f-new_provider").fill("Other Bank")
    page.locator("#f-new_payment").fill("1180")
    page.locator("#f-new_rate").fill("3.95")
    page.locator("#f-new_ends_on").fill("2033-03-31")
    page.get_by_role("button", name="Save renewal").click()
    expect(page.locator("#toast")).to_contain_text("Mortgage fix renewed until 31 Mar 2033")
    expect(page.locator("tbody.renewal", has_text="Mortgage fix")).to_contain_text("saved £1,320")

    page.goto("/networth")
    expect(page.locator("main")).to_contain_text("Other Bank")
    expect(page.locator("main")).to_contain_text("31 Mar 2033")

    page.goto("/renewals")
    page.locator("tbody.renewal", has_text="Mortgage fix").get_by_role("link", name="Renew Mortgage fix").click()
    page.get_by_role("button", name="Undo last renewal").click()
    page.locator("dialog#confirm").get_by_role("button", name="Undo").click()
    expect(page.locator("#toast")).to_contain_text("Last renewal of Mortgage fix undone")
    page.goto("/networth")
    expect(page.locator("main")).to_contain_text("Example Home Loans")
    expect(page.locator("main")).not_to_contain_text("31 Mar 2033")


def test_renew_form_fits_a_phone(page):
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto("/renewals")
    page.locator("tbody.renewal", has_text="Car insurance").get_by_role("link", name="Renew Car insurance").click()
    assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0
    tops = {round(page.get_by_text(o, exact=True).bounding_box()["y"]) for o in ("Stayed", "Switched", "Haggled")}
    assert len(tops) == 1, "the outcome choices should sit on one line"
