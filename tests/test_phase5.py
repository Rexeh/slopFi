"""Phase 5: Accounts and statements, Categories and rules, Review. Display names, confirmations with counts,
blocked deletes, the rule switch, and closing a month."""
import re
from datetime import date

from fastapi.testclient import TestClient

from slopfi import db, importer, review
from slopfi.web.app import app
from factories import JOINT_STATEMENT_RULES, add_rules, joint_statement as _stmt

RAW_ENUM = re.compile(r">\s*(alex|sam|joint|unknown|credit_card|current|savings|expense|income|transfer|contains|prefix|regex|manual|seed)\s*<")


def _seeded(db_path):
    conn = db.connect(db_path)
    add_rules(conn, JOINT_STATEMENT_RULES)
    importer.import_statement(conn, _stmt(), "a.pdf", "hash1")
    return conn


def _flash(r) -> str:
    from urllib.parse import unquote
    return unquote(r.headers.get("set-cookie", ""))


# ------------------------------------------------------------- statements
def test_statements_page_shows_display_names_and_both_tabs(db_path):
    _seeded(db_path).close()
    page = TestClient(app).get("/statements").text
    assert not RAW_ENUM.search(page), RAW_ENUM.search(page)
    assert 'role="tab" id="tab-statements"' in page and 'role="tab" id="tab-accounts"' in page
    assert "Import from configured folders" in page and "Remove statements whose files are gone" in page
    assert "Import a statement" in page and "Detect from the file" in page and "Imported statements" in page
    assert "<th scope=\"col\" class=\"num\">Paid in</th>" in page and ">Count</th>" in page
    assert "26 Jun 2026 to 25 Jul 2026" in page                                   # dates, never ISO or arrows
    assert "Joint</td><td>Current account</td>" in page                            # owner and type as display names
    assert 'data-confirm="Delete a.pdf?"' in page and "has 5 transactions" in page  # names the file and the count
    assert "Edit HSBC" in page and 'data-edit="acct-edit-1"' in page
    assert "Delete HSBC" not in page                                                 # an account with data has no Delete


def test_statement_delete_names_file_and_count(db_path):
    _seeded(db_path).close()
    client = TestClient(app)
    r = client.post("/statements/1/delete", follow_redirects=False)
    assert r.status_code == 303 and "a.pdf deleted, 5 transactions removed" in _flash(r)
    assert db.connect(db_path).execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    page = client.get("/statements").text
    assert "No statements yet" in page and "Delete HSBC" in page                   # now empty: Delete appears


def test_account_delete_blocked_until_empty_and_edit_saves(db_path):
    _seeded(db_path).close()
    client = TestClient(app)
    r = client.post("/accounts/1/delete", follow_redirects=False)
    assert "still has statements" in _flash(r) and '"error"' in _flash(r)
    assert db.connect(db_path).execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
    r = client.post("/accounts/1", data={"name": "HSBC Joint", "owner": "joint", "kind": "current"}, follow_redirects=False)
    assert r.headers["location"] == "/statements#accounts" and "HSBC Joint saved" in _flash(r)
    r = client.post("/accounts/1", data={"name": "X", "owner": "nobody", "kind": "current"}, follow_redirects=False)
    assert "not saved" in _flash(r)
    client.post("/statements/1/delete")
    r = client.post("/accounts/1/delete", follow_redirects=False)
    assert "HSBC Joint deleted" in _flash(r)


def test_sync_prune_confirmation_carries_count(db_path, monkeypatch, tmp_path):
    conn = _seeded(db_path)
    importer.import_statement(conn, _stmt(("2026-07-26", "2026-08-25")), "gone.pdf", "hash2")
    conn.close()
    src = tmp_path / "bank"
    src.mkdir()
    (src / "a.pdf").write_bytes(b"")
    (tmp_path / "sources.toml").write_text(f'[[source]]\npath = "bank"\naccount = "HSBC Joint"\n')
    monkeypatch.setenv("SLOPFI_SOURCES", str(tmp_path / "sources.toml"))
    page = TestClient(app).get("/statements").text
    assert 'data-prune-title="Remove 1 statement whose files are gone?"' in page
    assert "gone.pdf: 5 transactions go with them" in page


# ------------------------------------------------------------- categories
def test_categories_page_tabs_names_and_dialogs(db_path):
    _seeded(db_path).close()
    page = TestClient(app).get("/categories").text
    assert not RAW_ENUM.search(page), RAW_ENUM.search(page)
    assert "<h2>All categories</h2>" in page and 'id="panel-rules"' in page
    assert '<dialog class="form-dialog" id="add-category"' in page and '<dialog class="form-dialog" id="add-rule"' in page
    assert "Spending</td>" in page and "Starts with" in page or "Contains" in page
    assert 'role="switch" aria-checked="true"' in page and 'hx-post="/rules/1/toggle"' in page
    assert "Rename Groceries" in page and 'data-confirm="Delete rule ' in page and "they keep their category" in page
    assert "In use: " in page and "Apply to uncategorised" in page
    assert "opacity" not in page                                                     # disabled rows use ink-2 and a badge


def test_category_delete_blocked_when_in_use_and_rename(db_path):
    conn = _seeded(db_path)
    groceries = db.category_id_by_path(conn, "Groceries")
    conn.close()
    client = TestClient(app)
    r = client.post(f"/categories/{groceries}/delete", follow_redirects=False)
    assert "Groceries is in use (" in _flash(r) and "so it was not deleted" in _flash(r)
    assert db.connect(db_path).execute("SELECT COUNT(*) FROM categories WHERE id = ?", (groceries,)).fetchone()[0] == 1
    r = client.post("/categories", data={"name": "Llamas", "kind": "expense"}, follow_redirects=False)
    assert "Llamas added" in _flash(r)
    pets = db.connect(db_path).execute("SELECT id FROM categories WHERE name = 'Llamas'").fetchone()[0]
    page = client.get("/categories").text
    assert f'data-confirm="Delete Llamas?"' in page and f'form="cat-form-{pets}"' in page
    r = client.post(f"/categories/{pets}/rename", data={"name": "Animals"}, follow_redirects=False)
    assert "Renamed to Animals" in _flash(r)
    r = client.post(f"/categories/{pets}/delete", follow_redirects=False)
    assert "Animals deleted" in _flash(r)


def test_rule_switch_swaps_row_and_toasts(db_path):
    _seeded(db_path).close()
    client = TestClient(app)
    r = client.post("/rules/1/toggle", headers={"HX-Request": "true"})
    assert r.status_code == 200 and r.text.lstrip().startswith('<tr id="rule-1" class="disabled">')
    assert 'aria-checked="false"' in r.text and ">Disabled</span>" in r.text and 'id="rule-1-switch"' in r.text
    assert "disabled" in r.headers["HX-Trigger"]
    r = client.post("/rules/1/toggle", follow_redirects=False)                      # plain POST still works
    assert r.status_code == 303 and r.headers["location"] == "/categories#rules" and "enabled" in _flash(r)
    r = client.post("/rules/1/delete", follow_redirects=False)
    assert "Rule " in _flash(r) and " deleted" in _flash(r)


def test_bad_rule_reopens_the_dialog_on_the_rules_tab(db_path):
    _seeded(db_path).close()
    r = TestClient(app).post("/rules", data={"pattern": "(", "category_id": "1", "match_type": "regex"})
    assert r.status_code == 400 and "compile" in r.text and 'id="err-pattern"' in r.text
    assert 'id="tab-rules" aria-controls="panel-rules" aria-selected="true"' in r.text and "data-open" in r.text
    assert 'id="panel-categories" aria-labelledby="tab-categories" hidden' in r.text


# ----------------------------------------------------------------- review
def test_review_page_steps_and_close_button(db_path):
    _seeded(db_path).close()
    client = TestClient(app)
    page = client.get("/review?month=2026-06").text
    assert "<h1>Review · June 2026</h1>" in page and "June is open." in page
    assert page.count('<li class="step ') == 5 and "File them (2)" in page
    assert 'disabled aria-describedby="close-why-page"' in page and "Enabled once every step is done." in page
    assert 'name="month"' in page and ">Apply</button>" in page
    assert "Reopen" not in page.split("<main")[1]


def test_close_and_reopen_through_the_routes(db_path):
    conn = _seeded(db_path)
    conn.execute("UPDATE transactions SET category_id = 1, categorised_by = 'manual' WHERE category_id IS NULL")
    conn.commit()
    conn.close()
    client = TestClient(app)
    r = client.post("/review/close", data={"month": "2026-07"}, follow_redirects=False)
    assert r.status_code == 303
    conn = db.connect(db_path)
    if "2026-07" not in review.closed_months(conn):                                  # a stale balance can block it
        review.close_month(conn, "2026-07")
    conn.close()
    page = client.get("/review?month=2026-07").text
    assert "July is closed." in page and "Reopen July" in page and "Closed months" in page and "July 2026" in page
    r = client.post("/review/close", data={"month": "2026-07"}, follow_redirects=False)
    assert "already closed" in _flash(r)
    r = client.post("/review/reopen", data={"month": "2026-07"}, follow_redirects=False)
    assert r.headers["location"] == "/review?month=2026-07" and "July 2026 reopened" in _flash(r)
    assert review.closed_months(db.connect(db_path)) == []


def test_review_empty_state(db_path):
    page = TestClient(app).get("/review").text
    assert "Nothing imported yet" in page and 'href="/statements"' in page and "Go to Statements" in page


def test_coverage_gap_wording(conn):
    importer.import_statement(conn, _stmt(), "a.pdf", "hash1")
    june, july, sept = (review.coverage_gaps(conn, m) for m in ("2026-06", "2026-07", "2026-09"))
    assert [(g["name"], g["imported"], g["needed"]) for g in june + july + sept] == [
        ("HSBC UK current …1020", "imported from 26 Jun", "1–25 Jun"),
        ("HSBC UK current …1020", "imported up to 25 Jul", "26–31 Jul"),
        ("HSBC UK current …1020", "nothing imported for September", "all of September"),
    ]
    assert july[0]["text"] == "HSBC UK current …1020 needs 26–31 Jul"
    step = next(s for s in review.checklist(conn, "2026-07", today=date(2026, 8, 3))["steps"] if s["key"] == "statements")
    assert step["detail"] == "HSBC UK current …1020 needs 26–31 Jul." and step["gaps"] == july
    assert review.date_label("2026-09-05") == "5 Sep 2026" and review.date_label(None) == "never"
    stale = review.checklist(conn, "2026-06", today=date(2026, 12, 1))
    assert "last updated 25 Jul 2026" in next(s for s in stale["steps"] if s["key"] == "balances")["detail"]


def test_review_page_lists_each_statement_gap(db_path):
    conn = db.connect(db_path)
    importer.import_statement(conn, _stmt(), "a.pdf", "hash1")
    conn.close()
    html = TestClient(app).get("/review?month=2026-07").text
    step = html[html.index('id="step-statements"'):html.index('id="step-categorise"')]
    assert "Every account needs statements covering all of July" in step          # says what to do
    assert "<b>HSBC UK current …1020</b> imported up to 25 Jul · <b>26–31 Jul</b> still to import" in step


def test_no_raw_enums_or_bare_confirms_in_setup_templates():
    from pathlib import Path
    tdir = Path(__file__).resolve().parents[1] / "src" / "slopfi" / "web" / "templates"
    for name in ("statements.html", "categories.html", "_rules.html", "_rule_row.html", "review.html"):
        text = (tdir / name).read_text()
        assert "onchange=" not in text and "confirm(" not in text and 'style="' not in text, name
        for form in re.findall(r"<form[^>]+/delete[^>]*>", text):
            assert "data-confirm=" in form and "data-confirm-detail=" in form, (name, form)
