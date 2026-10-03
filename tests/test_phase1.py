"""Phase 1 foundations: shell, checklist, closed months, flashes, numeric validation, focus-safe row swaps."""
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from slopfi import db, importer, review
from slopfi.web.app import app
from factories import JOINT_STATEMENT_RULES, add_rules, joint_statement as _stmt

SRC = Path(__file__).resolve().parents[1] / "src" / "slopfi"


def _seeded(db_path):
    conn = db.connect(db_path)
    add_rules(conn, JOINT_STATEMENT_RULES)
    importer.import_statement(conn, _stmt(), "a.pdf", "hash1")
    return conn


# ------------------------------------------------------------------ schema
def test_schema_v7_has_closed_months(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
    assert conn.execute("SELECT COUNT(*) FROM closed_months").fetchone()[0] == 0


def test_migration_from_v6_adds_closed_months(tmp_path):
    path = tmp_path / "old.db"
    c = db.connect(path)
    c.execute("DROP TABLE closed_months")
    c.execute("PRAGMA user_version = 6")
    c.close()
    c = db.connect(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 7
    assert c.execute("SELECT name FROM sqlite_master WHERE name = 'closed_months'").fetchone()
    c.close()


# --------------------------------------------------------------- checklist
def test_checklist_steps_for_latest_open_month(conn):
    add_rules(conn, JOINT_STATEMENT_RULES)
    importer.import_statement(conn, _stmt(), "a.pdf", "hash1")
    today = date(2026, 8, 3)
    assert review.latest_open_month(conn, today) == "2026-07"      # newest complete calendar month with data
    c = review.checklist(conn, today=today)
    assert c["month"] == "2026-07" and [s["key"] for s in c["steps"]] == ["statements", "categorise", "one_offs", "balances", "targets"]
    june = review.checklist(conn, "2026-06", today=today)
    cat = next(s for s in june["steps"] if s["key"] == "categorise")
    assert cat["done"] is False and cat["count"] == "2 left" and cat["href"] == "/transactions?month=2026-06&uncategorised=1"
    balances = next(s for s in june["steps"] if s["key"] == "balances")
    assert balances["done"] is True                                  # statement closing balance is 9 days old
    stale = review.checklist(conn, "2026-06", today=date(2026, 12, 1))
    assert next(s for s in stale["steps"] if s["key"] == "balances")["count"] == "1 stale"
    assert next(s for s in june["steps"] if s["key"] == "targets")["count"] == "none set"
    assert c["uncategorised_total"] == 2 and june["left"] == 5 - june["done"]


def test_close_and_reopen_month(conn):
    importer.import_statement(conn, _stmt(), "a.pdf", "hash1")
    review.close_month(conn, "2026-06")
    assert review.closed_months(conn) == ["2026-06"]
    c = review.checklist(conn, "2026-06", today=date(2026, 8, 3))
    assert c["closed"] is True and c["left"] == 0 and c["can_close"] is False
    assert review.latest_open_month(conn, date(2026, 8, 3)) == "2026-07"
    review.close_month(conn, "2026-07")
    assert review.latest_open_month(conn, date(2026, 8, 3)) is None
    review.reopen_month(conn, "2026-06")
    assert review.closed_months(conn) == ["2026-07"]


def test_review_routes(db_path):
    _seeded(db_path).close()
    client = TestClient(app)
    page = client.get("/review").text
    assert "Review · " in page and "Statements imported" in page and 'id="checklist"' not in page   # page is the checklist
    partial = client.get("/review/checklist").text
    assert partial.startswith("\n") or partial.lstrip().startswith("<section")
    assert 'hx-get="/review/checklist"' in partial and "Categorise" in partial
    r = client.post("/review/close", data={"month": "2026-06"}, follow_redirects=False)
    assert r.status_code == 303 and "not ready" in r.headers["set-cookie"].replace("%20", " ")
    conn = db.connect(db_path)
    review.close_month(conn, "2026-07")                         # the sidebar follows the latest open month
    conn.close()
    assert "Close June" in client.get("/").text
    conn = db.connect(db_path)
    review.close_month(conn, "2026-06")                         # every month closed: the newest one, collapsed
    conn.close()
    assert "July closed" in client.get("/").text
    r = client.post("/review/reopen", data={"month": "2026-07"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/review?month=2026-07"
    assert "Close July" in client.get("/").text


# ------------------------------------------------------------------- shell
def test_shell_on_every_page(db_path):
    _seeded(db_path).close()
    client = TestClient(app)
    for path, current, title in [("/", "/", "Overview · Jul 2026 · slopFi"), ("/transactions", "/transactions", "Transactions · slopFi"),
                                 ("/networth", "/networth", "Net worth · slopFi"), ("/spending", "/spending", "Spending · slopFi"),
                                 ("/recurring", "/spending", "Spending · slopFi"), ("/categories", "/categories", "Categories and rules · slopFi")]:
        page = client.get(path).text
        assert f"<title>{title}</title>" in page, path
        assert '<nav aria-label="Main">' in page and 'class="skip"' in page and '<div id="toast" role="status"' in page, path
        assert f'href="{current}" data-label=' in page and page.count('aria-current="page"') == 1, path
        assert f'<a href="{current}" data-label="' in page.split('aria-current="page"')[0][-200:], path
        assert 'class="brand-wordmark"' in page and 'alt="slopFi"' in page and 'id="i-keyboard"' in page, path
    assert 'Checklist' not in client.get("/review").text.split('<main')[0]   # the sidebar widget is hidden on Review


def test_old_urls_redirect(db_path):
    client = TestClient(app)
    r = client.get("/trends?months=6&account_id=", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/spending?months=6&account_id="
    r = client.get("/rules", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/categories#rules"
    assert 'id="panel-rules"' in client.get("/categories").text


# ----------------------------------------------------------------- flashes
def test_flash_travels_in_a_cookie_not_the_query(db_path):
    client = TestClient(app)
    r = client.post("/settings", data={"proj.inflation": "3.4"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    assert "flash=" in r.headers["set-cookie"] and "Settings%20saved" in r.headers["set-cookie"]
    r = client.post("/rules/apply", follow_redirects=False)
    assert r.headers["location"] == "/categories#rules" and "msg=" not in r.headers["location"]
    for path in SRC.rglob("*.py"):
        assert "msg=" not in path.read_text(), path
    for path in (SRC / "web" / "templates").glob("*.html"):
        assert "msg" not in path.read_text(), path


# --------------------------------------------------- numeric field errors
@pytest.mark.parametrize("path, data, field", [
    ("/holdings", {"name": "ISA", "kind": "cash_isa", "value": "twelve", "valued_at": "2026-10-01"}, "value"),
    ("/holdings", {"name": "ISA", "kind": "cash_isa", "value": "100", "valued_at": "2026-10-01", "rate": "four"}, "rate"),
    ("/accounts/1/balance", {"date": "2026-10-01", "balance": "lots"}, "balance_1"),
    ("/settings", {"proj.inflation": "abc"}, "proj.inflation"),
    ("/rules", {"pattern": "X", "category_id": "1", "amount_min": "ten"}, "amount_min"),
    ("/rules", {"pattern": "X", "category_id": "1", "priority": "high"}, "priority"),
    ("/projection/1/assumptions", {"inflation_pct": "three"}, "inflation_pct"),
    ("/projection/1/assumptions", {"horizon_years": "ten"}, "horizon_years"),
    ("/projection/1/levers", {"surplus_override": "£abc"}, "surplus_override"),
])
def test_non_numeric_input_renders_a_field_error(db_path, path, data, field):
    _seeded(db_path).close()
    client = TestClient(app)
    r = client.post(path, data=data, follow_redirects=False)
    assert r.status_code == 400, (path, r.status_code)
    assert "Enter a number" in r.text and f'id="err-{field}"' in r.text and 'aria-invalid="true"' in r.text


def test_budget_target_error_rerenders_spending(db_path):
    conn = _seeded(db_path)
    groceries = db.category_id_by_path(conn, "Groceries")
    conn.close()
    client = TestClient(app)
    r = client.post(f"/budgets/{groceries}", data={"target": "lots", "next": "/spending?months=3&include_partial=1"})
    assert r.status_code == 400 and f'id="err-target_{groceries}"' in r.text and "Groceries" in r.text
    r = client.post(f"/budgets/{groceries}", data={"target": "£250", "next": "/spending?months=3"}, follow_redirects=False)
    assert r.status_code == 303 and "Target%20saved" in r.headers["set-cookie"]


def test_projection_api_rejects_junk_without_500(db_path):
    client = TestClient(app)
    r = client.get("/api/projection?surplus_override=abc")
    assert r.status_code == 400 and r.json()["errors"]["surplus_override"]
    assert client.get("/api/projection?surplus_override=120").status_code == 200


# ------------------------------------------------------------- row swaps
def test_row_swap_keeps_ids_and_refreshes_checklist(db_path):
    conn = _seeded(db_path)
    txn = conn.execute("SELECT id FROM transactions WHERE description LIKE 'UNKNOWN SHOP%'").fetchone()["id"]
    gifts = db.category_id_by_path(conn, "Shopping/Gifts")
    conn.close()
    client = TestClient(app)
    page = client.get("/transactions?uncategorised=1").text
    assert f'id="txn-{txn}-category"' in page
    r = client.post(f"/transactions/{txn}/category", data={"category_id": str(gifts)})
    assert r.status_code == 200
    assert f'id="txn-{txn}"' in r.text and f'id="txn-{txn}-category"' in r.text and f'id="txn-{txn}-one-off"' in r.text
    assert '"refresh": true' in r.headers["HX-Trigger"] and "Filed under Gifts" in r.headers["HX-Trigger"]
    r = client.post(f"/transactions/{txn}/one_off", data={"one_off": "1"})
    assert f'id="txn-{txn}-one-off"' in r.text and '"refresh": true' in r.headers["HX-Trigger"]


def test_static_budgets():
    css = (SRC / "web" / "static" / "style.css").read_text().splitlines()
    js = (SRC / "web" / "static" / "app.js").read_text().splitlines()
    assert len(css) <= 900 and len(js) <= 250
    body = "\n".join(line for line in css if not line.lstrip().startswith("--"))
    import re
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", body), "raw colour outside the token blocks"
