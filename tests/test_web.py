import re

from fastapi.testclient import TestClient

from slopfi import db, importer
from slopfi.web.app import app
from factories import JOINT_STATEMENT_RULES, add_rules, joint_statement as _stmt


def test_pages_render_empty(db_path):
    client = TestClient(app)
    for path in ("/", "/transactions", "/recurring", "/categories", "/rules", "/statements", "/health", "/trends",
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
