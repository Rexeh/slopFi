"""Net worth (update balances, the asset dialog) and Projection (levers repaint the tiles live, reset, save)."""
import re

from playwright.sync_api import expect


def test_update_balances_opens_and_cancels(page):
    page.goto("/networth")
    toggle = page.locator("#update-balances")
    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "true")
    first = page.locator("#balances-form .editing input:not([type=date])").first
    expect(first).to_be_visible()
    expect(first).to_be_focused()
    page.locator("[data-update-cancel]").click()
    expect(toggle).to_have_attribute("aria-pressed", "false")
    expect(first).to_be_hidden()


def test_add_an_asset_through_the_dialog(page):
    page.goto("/networth")
    opener = page.locator("button[data-open-holding]").first
    opener.click()
    dialog = page.locator("#holding-dialog")
    expect(dialog).to_be_visible()
    expect(page.locator("#holding-title")).to_have_text("Add an asset or liability")
    expect(page.locator("#holding-submit")).to_have_text("Add asset")

    kind = page.locator("#h-kind")
    kind.select_option("property")
    expect(page.locator("#h-mortgage-fields")).to_be_visible()
    kind.select_option("stocks_isa")
    expect(page.locator("#h-mortgage-fields")).to_be_hidden()
    expect(page.locator("#h-loan-fields")).to_be_hidden()

    page.keyboard.press("Escape")                                    # closing hands focus back to the opener
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()

    opener.click()
    kind.select_option("stocks_isa")
    page.locator("#h-name").fill("Test ISA")
    page.locator("#h-value").fill("12345")
    page.locator("#holding-submit").click()
    expect(page.locator("#toast")).to_contain_text("Test ISA")
    expect(page.locator("main")).to_contain_text("Test ISA")


def test_edit_an_asset_prefills_the_dialog(page):
    page.goto("/networth")
    edit = page.locator("button[data-edit-holding]:visible").first
    name = re.sub(r"^Edit | on its property$", "", edit.get_attribute("aria-label"))
    edit.click()
    expect(page.locator("#holding-title")).to_have_text(f"Edit {name}")
    expect(page.locator("#holding-submit")).to_have_text("Save changes")
    expect(page.locator("#h-name")).to_have_value(name)
    expect(page.locator("#holding-delete")).to_be_visible()


def test_levers_repaint_live_reset_and_save(page):
    page.goto("/projection")
    lever = page.locator("input.lever").first
    start = lever.input_value()
    lever.focus()
    page.keyboard.press("ArrowRight")                                # one 5% step, as a keyboard user would
    expect(lever).not_to_have_value(start)
    expect(page.locator("#save-bar")).to_be_visible()
    expect(page.locator("#save-bar-text")).to_contain_text("1 lever changed")
    expect(page.locator("#tiles .strip-delta").first).to_contain_text(re.compile(r"vs saved"))
    expect(page.locator("#lever-status")).to_contain_text("Levers save")

    page.locator("#levers-reset").click()
    expect(lever).to_have_value(start)
    expect(page.locator("#save-bar")).to_be_hidden()
    expect(lever).to_be_focused()

    page.keyboard.press("ArrowRight")
    page.get_by_role("button", name="Save to scenario").click()
    expect(page.locator("#toast")).to_contain_text("Levers saved to scenario")
    expect(page.locator("input.lever").first).not_to_have_value(start)   # the saved value survives the reload


def test_projection_chart_table_twin(page):
    page.goto("/projection")
    toggle = page.locator("#chart-table-toggle")
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#twin-projection")).to_be_visible()
    expect(page.locator("#chart-projection-wrap")).to_be_hidden()
