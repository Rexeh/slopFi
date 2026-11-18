import re
from datetime import date, timedelta

from fastapi.testclient import TestClient

from slopfi import db, importer
from slopfi.web.app import app
from factories import JOINT_STATEMENT_RULES, add_rules, joint_statement as _stmt


def test_pages_render_empty(db_path):
    client = TestClient(app)
    for path in ("/", "/renewals", "/transactions", "/recurring", "/categories", "/rules", "/statements", "/health", "/trends",
                 "/trends?months=6&account_id=&include_partial=1&include_one_off=1",
                 "/?month=&account_id=", "/transactions?month=all&category_id=&account_id=&uncategorised=&q=",
                 "/export/transactions.csv?month=&category_id=&account_id=&uncategorised="):
        r = client.get(path)
        assert r.status_code == 200, path


def test_pages_with_data_and_manual_categorise(db_path):
    conn = db.connect(db_path)
    importer.import_statement(conn, _stmt(), "a.pdf", "hash1")
    txn = conn.execute("SELECT id FROM transactions WHERE description LIKE 'UNKNOWN SHOP%'").fetchone()["id"]
    gifts = db.category_id_by_path(conn, "Shopping/Gifts")
    conn.close()

    client = TestClient(app)
    assert "Jun 2026" in client.get("/").text
    assert "UNKNOWN SHOP" in client.get("/transactions?uncategorised=1").text
    r = client.post(f"/transactions/{txn}/category", data={"category_id": str(gifts), "create_rule": "1", "pattern": "UNKNOWN SHOP"})
    assert r.status_code == 200 and r.headers.get("HX-Refresh") == "true"
    assert "UNKNOWN SHOP" not in client.get("/transactions?uncategorised=1").text
    csv = client.get("/export/transactions.csv").text
    assert "Gifts" in csv and csv.count("\n") >= 5


def test_trends_budget_and_one_off_routes(db_path):
    conn = db.connect(db_path)
    add_rules(conn, JOINT_STATEMENT_RULES)
    importer.import_statement(conn, _stmt(), "a.pdf", "hash1")
    txn = conn.execute("SELECT id FROM transactions WHERE description LIKE 'TESCO%'").fetchone()["id"]
    groceries = db.category_id_by_path(conn, "Groceries")
    conn.close()
    client = TestClient(app)
    r = client.post(f"/budgets/{groceries}", data={"target": "£250", "next": "/trends?months=3"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/trends?months=3"
    page = client.get("/trends?include_partial=1").text
    assert "250" in page and "Groceries" in page
    r = client.post(f"/transactions/{txn}/one_off", data={"one_off": "1"})
    assert r.status_code == 200 and "one-off" in r.text and "checked" in r.text
    conn = db.connect(db_path)
    assert conn.execute("SELECT one_off FROM transactions WHERE id = ?", (txn,)).fetchone()[0] == 1


def test_make_a_rule_opts_out_of_form_restore(db_path):
    """Saving with a rule sends HX-Refresh; on reload Firefox restores checkbox state by position, so a ticked
    "Make a rule" box would land on whichever row now sits in that slot. (Row Select/One-off are reset by transactions.js.)"""
    conn = db.connect(db_path)
    importer.import_statement(conn, _stmt(), "a.pdf", "hash1")
    conn.close()
    page = TestClient(app).get("/transactions?uncategorised=1").text
    boxes = re.findall(r'<input type="checkbox" name="create_rule"[^>]*>', page)
    assert boxes and all('autocomplete="off"' in b for b in boxes)


# ------------------------------------------------------------------ renewals
def _renewal_form(**over):
    from datetime import date, timedelta
    return {"name": "Car insurance", "kind": "insurance", "provider": "Northgate", "owner": "joint",
            "ends_on": (date.today() + timedelta(days=30)).isoformat(), "lead_days": "", "price": "612",
            "price_period": "year", "payee_key": "", "notes": "", **over}


def test_renewals_page_renders_empty_and_with_rows(db_path):
    client = TestClient(app)
    empty = client.get("/renewals").text
    assert "Contracts and dates that need a decision before they roll over" in empty and "No renewals yet" in empty
    r = client.post("/renewals", data=_renewal_form(), follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/renewals").text
    due = page.split("Due before the next close")[1]
    assert "Car insurance" in due and "£612.00 a year" in due and "paid yearly" in due
    assert '<span class="count" aria-label="1 due">1</span>' in page


def test_renew_flow_records_and_toasts(db_path):
    from datetime import date, timedelta
    from urllib.parse import unquote
    client = TestClient(app)
    client.post("/renewals", data=_renewal_form())
    rid = db.connect(db_path).execute("SELECT id FROM renewals").fetchone()["id"]
    form = client.get(f"/renewals?renew={rid}").text
    assert 'name="outcome"' in form and "Renewal quote" in form
    new_end = (date.today() + timedelta(days=395)).isoformat()
    r = client.post(f"/renewals/{rid}/renew", data={"outcome": "switched", "quote": "734", "quote_period": "year",
                                                     "other_quote": "559", "other_period": "year", "other_provider": "Kestrel Direct",
                                                     "new_provider": "Kestrel Direct", "new_price": "559", "new_period": "year",
                                                     "new_ends_on": new_end}, follow_redirects=False)
    assert r.status_code == 303 and "renewed until" in unquote(r.headers["set-cookie"])
    page = client.get(f"/renewals?renew={rid}").text
    assert "saved £175" in page and "Kestrel Direct" in page


def test_validation_rejects_missing_end_date(db_path):
    client = TestClient(app)
    r = client.post("/renewals", data=_renewal_form(ends_on=""))
    assert r.status_code == 400 and 'id="err-ends_on"' in r.text and "Enter the date it ends." in r.text
    r = client.post("/renewals", data=_renewal_form(price="lots"))
    assert r.status_code == 400 and 'id="err-price"' in r.text


def test_mortgage_row_has_no_delete(db_path):
    conn = db.connect(db_path)
    from slopfi import reports
    reports.save_property(conn, {"name": "1 Example Street", "kind": "property", "owner": "joint", "provider": None,
                                 "notes": None, "value": 385000.0, "valued_at": "2026-10-31"},
                          {"provider": "Example Home Loans", "rate": 4.29, "monthly_payment": 1145.0, "value": 212390.0,
                           "fix_end": "2027-03-31", "term_end": "2051-06-30"})
    conn.close()
    client = TestClient(app)
    page = client.get("/renewals").text
    assert "From Net worth" in page
    rid = db.connect(db_path).execute("SELECT id FROM renewals").fetchone()["id"]
    edit = client.get(f"/renewals?edit={rid}").text
    assert f"/renewals/{rid}/delete" not in edit


def _mortgage(db_path):
    from slopfi import reports
    conn = db.connect(db_path)
    reports.save_property(conn, {"name": "1 Example Street", "kind": "property", "owner": "joint", "provider": None,
                                 "notes": None, "value": 385000.0, "valued_at": "2026-10-31"},
                          {"provider": "Example Home Loans", "rate": 4.29, "monthly_payment": 1145.0, "value": 212390.0,
                           "fix_end": (date.today() + timedelta(days=100)).isoformat(), "term_end": "2051-06-30"})
    client = TestClient(app)
    client.get("/renewals")   # syncs the mortgage row
    return client, conn.execute("SELECT id FROM renewals").fetchone()["id"], conn


def test_blank_new_price_is_rejected(db_path):
    client = TestClient(app)
    client.post("/renewals", data=_renewal_form())
    rid = db.connect(db_path).execute("SELECT id FROM renewals").fetchone()["id"]
    r = client.post(f"/renewals/{rid}/renew", data={"outcome": "stayed", "new_price": "", "new_period": "year",
                                                     "new_ends_on": (date.today() + timedelta(days=400)).isoformat()})
    assert r.status_code == 400 and 'id="err-new_price"' in r.text
    assert db.connect(db_path).execute("SELECT price FROM renewals").fetchone()["price"] == 612.0
    assert 'value="612.00"' in client.get(f"/renewals?renew={rid}").text   # prefilled with the current price


def test_mortgage_renewal_uses_monthly_payment_as_price(db_path):
    client, rid, conn = _mortgage(db_path)
    form = client.get(f"/renewals?renew={rid}").text
    assert 'id="f-new_price"' not in form and 'id="f-new_payment"' in form
    client.post(f"/renewals/{rid}/renew", data={"outcome": "switched", "quote": "1290", "quote_period": "month",
                                                "new_provider": "Other Bank", "new_payment": "1180", "new_rate": "3.95",
                                                "new_ends_on": (date.today() + timedelta(days=1900)).isoformat()})
    assert "saved £1,320" in client.get("/renewals").text


def test_archived_history_is_shown(db_path):
    client, rid, conn = _mortgage(db_path)
    client.post(f"/renewals/{rid}/renew", data={"outcome": "switched", "quote": "1290", "quote_period": "month",
                                                "new_provider": "Other Bank", "new_payment": "1180",
                                                "new_ends_on": (date.today() + timedelta(days=1900)).isoformat()})
    conn.execute("DELETE FROM holdings WHERE kind = 'property'")
    page = client.get("/renewals").text
    archived = page.split("Archived (1)")[1]
    assert "Example Home Loans → Other Bank" in archived and "Undo last renewal" not in archived


def test_lead_time_entered_in_months(db_path):
    client = TestClient(app)
    client.post("/renewals", data={**_renewal_form(), "lead_months": "4"})
    assert db.connect(db_path).execute("SELECT lead_days FROM renewals").fetchone()["lead_days"] == 122
    r = client.post("/renewals", data={**_renewal_form(), "lead_months": "40"})
    assert r.status_code == 400 and 'id="err-lead_months"' in r.text


def test_renew_form_offers_defer_for_due_rows(db_path):
    conn = db.connect(db_path)
    importer.import_statement(conn, _stmt(), "a.pdf", "hash1")
    conn.close()
    client = TestClient(app)
    client.post("/renewals", data=_renewal_form())
    rid = db.connect(db_path).execute("SELECT id FROM renewals").fetchone()["id"]
    assert 'action="/review/defer-renewal"' in client.get(f"/renewals?renew={rid}").text
