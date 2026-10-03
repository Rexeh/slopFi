"""Phase 3: Spending (one page, four tabs) and Net worth (balances mode, asset dialog, history)."""
import json
import re
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from slopfi import db, importer, reports, spending_views
from slopfi.web.app import app
from factories import ENERGY, EVERYDAY_RULES, SALARY, add_rules
from test_trends import _stmt, _t

WEB = Path(spending_views.__file__).parent / "web"


@pytest.fixture
def spend_db(db_path):
    """Jan–Jun 2026 on one fully covered account. Utilities jump from £10 to £300 a month (the "from £x" case),
    groceries are steady, and an uncategorised shop appears every month."""
    conn = db.connect(db_path)
    add_rules(conn, EVERYDAY_RULES)
    txns = []
    for i, m in enumerate(["01", "02", "03", "04", "05", "06"], start=1):
        txns += [
            _t(f"2026-{m}-05", "TESCO STORES", -300.0),
            _t(f"2026-{m}-10", "DELIVEROO", -(50.0 + 10 * i)),
            _t(f"2026-{m}-01", ENERGY, -(10.0 if i <= 3 else 300.0), "DD"),
            _t(f"2026-{m}-12", "MYSTERY TRADER LTD", -60.0, "VIS"),
            _t(f"2026-{m}-28", SALARY, 3000.0, "BACS"),
        ]
    importer.import_statement(conn, _stmt("2026-01-01", "2026-06-30", txns), "a.pdf", "h1")
    yield conn
    conn.close()


def _flash(response) -> str:
    return unquote(response.headers.get("set-cookie", ""))


# ------------------------------------------------------------------ redirects
def test_old_spending_addresses_redirect(db_path):
    client = TestClient(app)
    r = client.get("/trends?months=6", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/spending?months=6"
    r = client.get("/recurring", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/spending#fixed"
    r = client.get("/recurring?months=12&account_id=", follow_redirects=False)
    assert r.headers["location"] == "/spending?months=12&account_id=#fixed"
    assert client.get("/recurring").url.path == "/spending"
    for gone in ("trends.html", "recurring.html"):
        assert not (WEB / "templates" / gone).exists()


# ------------------------------------------------------------------- spending
def test_every_spending_tab_renders(spend_db):
    page = TestClient(app).get("/spending?months=3").text
    assert '<title>Spending · slopFi</title>' in page
    assert 'role="tablist"' in page
    for key, label in (("categories", "Categories"), ("fixed", "Fixed costs"), ("targets", "Targets"), ("ideas", "Savings ideas")):
        assert f'id="tab-{key}"' in page and f'aria-controls="panel-{key}"' in page and f">{label}</button>" in page
        assert f'id="panel-{key}"' in page
    assert re.search(r'id="panel-categories" aria-labelledby="tab-categories"\s*>', page)      # the open tab
    assert re.search(r'id="panel-fixed" aria-labelledby="tab-fixed"\s+hidden', page)           # the rest hidden
    # Categories: multiples, table with sparklines and share bars, matrix with its twin, month by month
    assert 'id="sm-0"' in page and "avg £" in page and 'id="chart-matrix"' in page and 'id="twin-matrix"' in page
    assert 'class="spark"' in page and 'class="share-bar"' in page and "Month by month" in page
    # Fixed costs: the recurring table
    fixed = page.split('id="panel-fixed"')[1].split('id="panel-targets"')[0]
    assert ENERGY in fixed and "Direct debit" in fixed
    # Savings ideas: ranked rows
    ideas = page.split('id="panel-ideas"')[1]
    assert '<ol class="ideas">' in ideas and "Categorise the unknowns" in ideas
    assert "How to use Spending" in page
    # the options disclosure and Apply, no auto-submit
    assert "Include months with gaps" in page and "Include one-offs" in page and ">Apply</button>" in page
    assert "onchange" not in page


def test_change_reads_from_the_previous_figure_not_a_huge_percentage(spend_db):
    page = TestClient(app).get("/spending?months=3").text
    assert "vs Jan–Mar" in page
    utilities = page.split(">Utilities</a>")[1].split("</tr>")[0]
    assert "+£290" in utilities and "(from £10)" in utilities
    assert "999%" not in page and "2900%" not in page
    assert spending_views.change_text(290.0, 10.0)["detail"] == "(from £10)"
    assert spending_views.change_text(30.0, 200.0)["detail"] == "(+15%)"
    assert spending_views.change_text(1.0, 900.0)["detail"] == ""


def test_table_total_matches_average_spending_and_sparklines_carry_pounds(spend_db):
    page = TestClient(app).get("/spending?months=3").text
    tile = re.search(r'Average spending</span><span class="figure">([^<]+)<', page).group(1)
    total = re.search(r'<td colspan="2">All spending</td><td class="num">([^<]+)<', page).group(1)
    assert tile == total
    assert ">Uncategorised</a>" in page                    # money with no category is a row, not missing
    labels = re.findall(r'class="spark" viewBox="0 0 96 24" role="img" aria-label="([^"]+)"', page)
    assert labels and all("£" in label for label in labels)
    assert any(label.startswith("£") and "each month" in label for label in labels)   # a flat series reads as one figure
    data = json.loads(re.search(r'<script id="spending-data" type="application/json">(.*?)</script>', page, re.S).group(1))
    housing_like = data["multiples"]["charts"][0]
    assert housing_like["avg"] == round(max(c["avg"] for c in data["multiples"]["charts"]))
    assert len(data["matrix"]["series"]) <= 8 and data["matrix"]["series"][-1]["name"] == "Uncategorised"


def test_targets_tab_set_and_remove(spend_db):
    groceries = db.category_id_by_path(spend_db, "Groceries")
    client = TestClient(app)
    page = client.get("/spending?months=3").text
    targets = page.split('id="panel-targets"')[1].split('id="panel-ideas"')[0]
    assert "No targets yet" in targets and "Start with the two or three" in targets
    assert f'<label class="visually-hidden" for="target_{groceries}">' in targets and '>Set</button>' in targets
    r = client.post(f"/budgets/{groceries}", data={"target": "£250", "next": "/spending?months=3#targets"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/spending?months=3#targets" and "Target saved" in _flash(r)
    page = client.get("/spending?months=3").text
    targets = page.split('id="panel-targets"')[1].split('id="panel-ideas"')[0]
    assert "No targets yet" not in targets and 'value="250"' in targets and "bullet-tick" in targets
    assert "1 set" in page                                            # the strip counts it
    r = client.post(f"/budgets/{groceries}", data={"target": "", "next": "/spending#targets"}, follow_redirects=False)
    assert "Target removed" in _flash(r) and reports.budgets(spend_db) == {}
    r = client.post(f"/budgets/{groceries}", data={"target": "lots", "next": "/spending?months=3"})
    assert r.status_code == 400 and f'id="err-target_{groceries}"' in r.text
    assert 'id="tab-targets" aria-controls="panel-targets" aria-selected="true"' in r.text   # re-rendered on Targets


def test_spending_empty_without_statements(db_path):
    page = TestClient(app).get("/spending").text
    assert "No complete month yet" in page and 'href="/statements"' in page


# ------------------------------------------------------------------ net worth
@pytest.fixture
def nw_db(db_path):
    conn = db.connect(db_path)
    importer.import_statement(conn, _stmt("2026-06-01", "2026-06-30", [_t("2026-06-03", "TESCO STORES", -20.0)]), "a.pdf", "h1")
    monzo = importer.get_or_create_account(conn, "Monzo Alex", "alex", "current")
    yield conn, monzo
    conn.close()


def test_update_balances_flow(nw_db):
    conn, monzo = nw_db
    client = TestClient(app)
    page = client.get("/networth").text
    assert re.search(r'<button type="button" class="btn btn-primary" id="update-balances"[^>]*>.*Update balances', page)
    assert "Never updated" in page                                    # the stale row's Updated cell
    form = page.split('id="balances-form"')[1].split("</form>")[0]
    assert f'name="balance_{monzo}"' in form and f'name="date_{monzo}"' in form and "Save balances" in form
    assert 'class="actions balances-actions" hidden' in form          # nothing leaks until Update balances
    opened = client.get("/networth?update=1").text
    assert 'balances-actions" hidden' not in opened and 'class="is-editing"' in opened
    r = client.post("/networth/balances", data={f"balance_{monzo}": "lots", f"date_{monzo}": "2026-10-02"})
    assert r.status_code == 400 and f'id="err-balance_{monzo}"' in r.text and 'aria-invalid="true"' in r.text
    r = client.post("/networth/balances", data={f"balance_{monzo}": "", f"date_{monzo}": "2026-10-02"}, follow_redirects=False)
    assert r.status_code == 303 and "No balances entered" in _flash(r)
    r = client.post("/networth/balances", data={f"balance_{monzo}": "£1,234.50", f"date_{monzo}": "2026-10-02"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/networth" and "Monzo Alex balance saved" in _flash(r)
    bal = {a["name"]: a for a in reports.account_balances(conn)}["Monzo Alex"]
    assert bal["balance"] == 1234.5 and bal["date"] == "2026-10-02"


def test_asset_dialog_add_edit_delete(nw_db):
    conn, _ = nw_db
    client = TestClient(app)
    page = client.get("/networth").text
    assert '<dialog class="holding-dialog" id="holding-dialog"' in page and 'id="holding-dialog" aria-labelledby="holding-title" open' not in page
    assert "data-open-holding" in page and "?edit=" not in page      # no reload links, no permanent form
    r = client.post("/holdings", data={"name": "Our house", "kind": "property", "owner": "joint", "value": "300,000",
                                       "valued_at": "2026-10-02", "mortgage_balance": "£150,000", "rate": "3.75",
                                       "monthly_payment": "800"}, follow_redirects=False)
    assert r.status_code == 303 and "Our house saved" in _flash(r)
    pid = conn.execute("SELECT id FROM holdings WHERE kind = 'property'").fetchone()[0]
    page = client.get("/networth").text
    assert f'data-edit-holding="{pid}"' in page and 'role="meter"' in page and 'aria-valuenow="50"' in page
    data = json.loads(re.search(r'<script id="networth-data" type="application/json">(.*?)</script>', page, re.S).group(1))
    house = next(h for h in data["holdings"] if h["id"] == pid)
    assert house["mortgage"]["value"] == 150000.0 and house["n_snapshots"] == 1   # what the dialog prefills from
    # edit: the same property, its mortgage kept and updated
    r = client.post("/holdings", data={"holding_id": str(pid), "name": "Our house", "kind": "property", "owner": "joint",
                                       "value": "310000", "valued_at": "2026-10-03", "mortgage_balance": "149000"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute("SELECT COUNT(*) FROM holdings WHERE kind = 'property'").fetchone()[0] == 1
    assert reports.net_worth(conn)["mortgage"] == 149000.0
    # a field error re-opens the dialog on the same asset with the message beside the field
    r = client.post("/holdings", data={"holding_id": str(pid), "name": "Our house", "kind": "property", "value": "lots",
                                       "valued_at": "2026-10-03"})
    assert r.status_code == 400 and 'id="err-value"' in r.text and 'aria-labelledby="holding-title" open' in r.text
    # delete lives inside the dialog and names the item and the count
    page = client.get(f"/networth?edit={pid}").text
    assert 'data-confirm="Delete Our house?"' in page and "2 valuations recorded; its mortgage goes with it" in page
    r = client.post(f"/holdings/{pid}/delete", follow_redirects=False)
    assert r.status_code == 303 and "Our house deleted" in _flash(r)
    assert conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 0


def test_history_empty_until_two_snapshots(nw_db):
    conn, monzo = nw_db
    client = TestClient(app)
    page = client.get("/networth").text
    assert "No history yet" in page and 'id="chart-history"' not in page
    reports.add_balance_snapshot(conn, monzo, "2026-09-01", 500.0)
    page = client.get("/networth").text
    assert "No history yet" in page and "One snapshot so far, on 1 Sep 2026" in page
    assert spending_views.history(conn) == []
    reports.add_balance_snapshot(conn, monzo, "2026-10-01", 800.0)
    page = client.get("/networth").text
    assert "No history yet" not in page and 'id="chart-history"' in page
    points = spending_views.history(conn)
    assert [p["date"] for p in points] == ["2026-09-01", "2026-10-01"]
    assert points[1]["value"] - points[0]["value"] == 300.0
    assert "since 1 Sep 2026" in page                                 # the Net worth tile's delta


def test_ltv_meter_ticks_and_dark_contrast():
    css = (WEB / "static" / "style.css").read_text()

    def lum(hex_):
        rgb = [int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    def ratio(a, b):
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    dark = css[css.index("prefers-color-scheme: dark"):]
    tok = lambda name, block: re.search(rf"{name}:\s*(#[0-9a-fA-F]{{6}})", block).group(1)
    assert ratio(tok("--ramp-2", dark), tok("--ramp-track", dark)) >= 3.0           # fill against track
    assert ratio(tok("--color-border-strong", dark), tok("--color-surface", dark)) >= 3.0   # the track's edge
    page = (WEB / "templates" / "networth.html").read_text()
    assert all(f'data-l="{b}%"' in page for b in (60, 75, 85))
