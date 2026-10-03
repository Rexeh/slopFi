"""Phase 2: Overview (typical, strip, bench tables, status line, fund target) and Transactions (filters, lean rows,
shared chooser, bulk routes, keyboard-ready markup, page weight)."""
import re
from datetime import date

from fastapi.testclient import TestClient

from slopfi import db, importer, overview, reports, review
from slopfi.parsers import ParsedTransaction
from slopfi.web.app import app
from slopfi.web.routes.transactions import resolve_category

from factories import CARD_ID, JOINT_HOLDER, JOINT_ID, MONZO_ID, add_rules, rule, statement

# Groceries (TESCO, filed by the rule below) per month: Mar–May average £200; June £230 is "about typical".
GROCERIES = {"2026-03": 100.0, "2026-04": 200.0, "2026-05": 300.0, "2026-06": 230.0}

RULES = [
    rule(r"\b(TESCO|SAINSBURYS)\b", "Groceries/Supermarket", "regex"),
    rule("TFL TRAVEL", "Transport/Public transport"),
    rule("PRET A MANGER", "Eating out/Restaurants & cafes"),
    rule("AMAZON", "Shopping/Online"),
    rule("DELIVEROO", "Eating out/Takeaway & delivery"),
    rule("BOOTS", "Health & personal care/Pharmacy", "prefix"),
]


def _statement(txns, start="2026-03-01", end="2026-06-30", ident=JOINT_ID, name=JOINT_HOLDER):
    return statement(txns, start, end, identifier=ident, name=name)


def _history(db_path):
    """Four months on one joint account: groceries each month, income each month, an unfiled £500 in June."""
    conn = db.connect(db_path)
    add_rules(conn, RULES)
    txns = []
    for m, amount in GROCERIES.items():
        y, mo = int(m[:4]), int(m[5:])
        txns.append(ParsedTransaction(date(y, mo, 3), ")))", ["TESCO STORES 2041", "EXAMPLETOWN"], -amount))
        txns.append(ParsedTransaction(date(y, mo, 25), "BP", ["ACME ANALYTICS LTD", "SALARY"], 2000.0, None,
                                      suggested_category="Income"))
    txns.append(ParsedTransaction(date(2026, 6, 12), "VIS", ["UNKNOWN SHOP", "EXAMPLETOWN"], -500.0))
    importer.import_statement(conn, _statement(txns), "h.pdf", "hash-h")
    return conn


# ------------------------------------------------------------------ typical
def test_typical_is_the_three_months_before_and_named(db_path):
    conn = _history(db_path)
    assert overview.typical_months(conn, "2026-06") == ["2026-03", "2026-04", "2026-05"]
    a = overview.against_typical(conn, "2026-06")
    assert a["typical_label"] == "Mar–May"
    groceries = next(r for r in a["rows"] if r["name"] == "Groceries")
    assert round(groceries["typical"]) == 200 and groceries["state"] == "about"   # +£30 is under £50
    uncat = next(r for r in a["rows"] if r["uncategorised"])
    assert uncat["spend"] == 500 and uncat["typical"] is None
    # the tile's total is the Month by month figure (monthly_summary outgoings) to the pound
    june = next(m for m in reports.monthly_summary(conn) if m["month"] == "2026-06")
    assert round(a["total"], 2) == round(june["outgoings"], 2)
    conn.close()


def test_month_range_and_money_formats():
    assert overview.month_range_label(["2026-06", "2026-07", "2026-08"]) == "Jun–Aug"
    assert overview.month_range_label(["2025-11", "2026-01"]) == "Nov 2025–Jan 2026"
    assert overview.money_tile(280371.7) == "£280.4k" and overview.money_tile(-5207.4) == "−£5,207"
    assert overview.money_delta(-922.2) == "−£922" and overview.money_delta(12) == "+£12"
    assert overview.money_pence(-26.5) == "−£26.50" and overview.date_label("2026-09-27") == "27 Sep 2026"


def test_six_months_flags_running_month_and_averages_full_months(db_path):
    conn = _history(db_path)
    six = overview.six_months(conn, today=date(2026, 6, 15))
    assert [m["month"] for m in six] == ["2026-03", "2026-04", "2026-05", "2026-06"]
    assert six[-1]["partial"] and six[-1]["avg3"] is None
    assert six[0]["avg3"] is None                    # one full month is not an average
    assert round(six[2]["avg3"]) == 200              # Mar–May
    conn.close()


# ------------------------------------------------------------------ overview page
def test_overview_page_strip_tables_and_charts(db_path):
    _history(db_path).close()
    html = TestClient(app).get("/?month=2026-06").text
    strip = html[html.index('class="strip"'):html.index('class="bench"')]
    assert "£730" in strip and "Mar–May" in strip                   # spending tile, typical named
    assert "Set a target" in strip                                  # no fund target yet
    typical = html[html.index('id="panel-typical"'):html.index('id="panel-six"')]
    assert "about typical" in typical and "<tfoot><tr><td>Total</td><td class=\"num\">£730</td>" in typical
    assert 'class="share-bar"' in typical
    # charts: headline aria-label, server-rendered twins, a Table toggle, no colours in the page
    assert re.search(r'<canvas id="chart-where" role="img" aria-label="Where June went: £730', html)
    assert 'id="twin-where"' in html and 'id="twin-six"' in html and 'data-table-toggle="six"' in html
    # filters: Apply, no auto-submit, account select hidden with one owner
    assert "onchange" not in html and ">Apply</button>" in html and 'id="account_id"' not in html
    assert "Where June went" in html and "Largest payments in June" in html and "Before you close June" in html


def test_overview_account_select_appears_with_two_owners(db_path):
    conn = _history(db_path)
    importer.import_statement(conn, _statement(
        [ParsedTransaction(date(2026, 6, 4), ")))", ["TESCO STORES 2041"], -5.0)], ident=CARD_ID, name="Alex card"),
        "c.pdf", "hash-c")
    conn.execute("UPDATE accounts SET owner = 'alex' WHERE identifier = ?", (CARD_ID,))
    conn.commit(); conn.close()
    assert 'id="account_id"' in TestClient(app).get("/?month=2026-06").text


def test_overview_defaults_to_latest_open_month(db_path):
    _history(db_path).close()
    assert "<h1>Overview · June 2026</h1>" in TestClient(app).get("/").text


def test_fund_target_saves_and_rejects_junk(db_path):
    _history(db_path).close()
    client = TestClient(app)
    r = client.post("/overview/fund-target", data={"target": "abc", "month": "2026-06"})
    assert r.status_code == 400 and 'id="err-fund_target"' in r.text and 'aria-invalid="true"' in r.text
    r = client.post("/overview/fund-target", data={"target": "100000", "month": "2026-06"}, follow_redirects=True)
    assert r.status_code == 200 and "of £100k" in r.text and "Change target" in r.text


def test_empty_overview(db_path):
    db.connect(db_path).close()
    html = TestClient(app).get("/").text
    assert "Nothing imported yet" in html and "chart-where" not in html


# ------------------------------------------------------------------ transactions
def test_resolve_category_accepts_label_leaf_prefix_and_id(conn):
    options = reports.category_options(conn)
    gifts = next(o["id"] for o in options if o["label"] == "Shopping / Gifts")
    assert resolve_category(options, "Shopping / Gifts") == (gifts, None)
    assert resolve_category(options, "gifts") == (gifts, None)
    assert resolve_category(options, str(gifts)) == (gifts, None)
    assert resolve_category(options, "") == (None, None)
    assert resolve_category(options, "zzz")[1] == "Choose a category from the list."


def test_transactions_filters_and_markup(db_path):
    _history(db_path).close()
    html = TestClient(app).get("/transactions?month=2026-06").text
    assert "onchange" not in html
    assert 'hx-trigger="input changed delay:300ms"' in html and html.count('hx-push-url="true"') >= 2
    assert html.count('<datalist id="cats">') == 1 and html.count('<template id="cat-editor">') == 1
    assert "3 transactions in Jun 2026" in html                      # result line
    # uncategorised rows open with the chooser; filed rows show text with a quiet edit
    unknown = re.search(r'<tr id="txn-(\d+)" class="uncat is-editing">.*?UNKNOWN SHOP', html).group(1)
    assert f'id="txn-{unknown}-category" placeholder="Choose a category"' in html
    assert re.search(r'<button class="cat" id="txn-\d+-category" aria-describedby="cat-hint">Groceries', html)
    assert ">Export CSV</a>" in html and 'href="/export/transactions.csv?month=2026-06' in html
    head = html[html.index("<thead>"):html.index("</thead>")]
    assert re.findall(r'<th scope="col"[^>]*>(?:<a [^>]*>)?([^<]*)<', head) == ["", "Date", "Account", "Description",
                                                                             "Amount", "Category", "Source", "One-off"]


def _descriptions(html):
    return [m.split(" / ")[0] for m in re.findall(r'<tr id="txn-\d+"[^>]*>(?:<td>.*?</td>){3}<td>([^<]*)', html)]


def test_transactions_sort_by_column(db_path):
    _history(db_path).close()
    client = TestClient(app)
    default = _descriptions(client.get("/transactions?month=2026-06").text)
    assert default == ["ACME ANALYTICS LTD", "UNKNOWN SHOP", "TESCO STORES 2041"]          # newest first
    html = client.get("/transactions?month=2026-06&sort=amount&dir=asc").text
    assert _descriptions(html) == ["UNKNOWN SHOP", "TESCO STORES 2041", "ACME ANALYTICS LTD"]
    assert _descriptions(client.get("/transactions?month=2026-06&sort=amount&dir=desc").text)[0] == "ACME ANALYTICS LTD"
    assert _descriptions(client.get("/transactions?month=2026-06&sort=description&dir=asc").text) == \
        ["ACME ANALYTICS LTD", "TESCO STORES 2041", "UNKNOWN SHOP"]
    assert _descriptions(client.get("/transactions?month=2026-06&sort=date&dir=asc").text) == default[::-1]
    # uncategorised sorts first ascending; unknown columns fall back to newest first
    assert _descriptions(client.get("/transactions?month=2026-06&sort=category&dir=asc").text)[0] == "UNKNOWN SHOP"
    assert _descriptions(client.get("/transactions?month=2026-06&sort=nonsense&dir=up").text) == default

    # the active header says so; its link flips the direction, others open on their own first direction
    head = html[html.index("<thead>"):html.index("</thead>")]
    assert re.findall(r'aria-sort="(\w+)"', head) == ["ascending"]
    assert re.search(r'<th scope="col" class="num" aria-sort="ascending"><a [^>]*data-sort="amount" data-dir="desc"', head)
    assert 'href="/transactions?month=2026-06&amp;category_id=&amp;q=&amp;uncategorised=&amp;account_id=&amp;sort=amount&amp;dir=desc"' in head
    assert 'data-sort="description" data-dir="asc"' in head and 'data-sort="one_off" data-dir="desc"' in head
    assert 'data-col="date" data-sort="" data-dir=""' in head        # newest first is the default: no sort in the link
    # the filter form carries the sort, so changing a filter keeps it; so do the bulk forms and the export
    assert '<input type="hidden" name="sort" value="amount"><input type="hidden" name="dir" value="asc">' in html
    assert 'name="sort" value="amount"' in html[html.index('class="bulk-form"'):]
    assert "sort=amount&amp;dir=asc" in html[html.index('class="export"'):]
    default_head = client.get("/transactions?month=2026-06").text
    assert re.search(r'aria-sort="descending"><a [^>]*data-col="date" data-sort="date" data-dir="asc"', default_head)


def test_bulk_and_export_keep_the_sort(db_path):
    _history(db_path).close()
    client = TestClient(app)
    hx = {"HX-Request": "true", "HX-Target": "txn-results"}
    r = client.post("/transactions/bulk/one_off", headers=hx, data={"ids": [], "month": "2026-06", "sort": "amount", "dir": "asc"})
    assert _descriptions(r.text) == ["UNKNOWN SHOP", "TESCO STORES 2041", "ACME ANALYTICS LTD"]
    csv = client.get("/export/transactions.csv?month=2026-06&sort=amount&dir=desc").text.splitlines()
    assert "ACME ANALYTICS LTD" in csv[1] and "UNKNOWN SHOP" in csv[-1]


def test_search_returns_only_the_results_region_to_htmx(db_path):
    _history(db_path).close()
    client = TestClient(app)
    r = client.get("/transactions?q=unknown", headers={"HX-Request": "true", "HX-Target": "txn-results"})
    assert r.text.lstrip().startswith('<div id="txn-results">') and "<html" not in r.text
    assert "1 transaction matching “unknown” across all months" in r.text


def test_row_category_by_label_and_error(db_path):
    conn = _history(db_path)
    txn = conn.execute("SELECT id FROM transactions WHERE description LIKE 'UNKNOWN SHOP%'").fetchone()["id"]
    conn.close()
    client = TestClient(app)
    r = client.post(f"/transactions/{txn}/category", data={"category": "nonsense"})
    assert r.status_code == 200 and 'aria-invalid="true"' in r.text and "Choose a category from the list." in r.text
    assert 'value="nonsense"' in r.text and "HX-Trigger" not in r.headers
    r = client.post(f"/transactions/{txn}/category", data={"category": "Shopping / Gifts"})
    assert "Filed under Gifts" in r.headers["HX-Trigger"] and f'<tr id="txn-{txn}"' in r.text


def test_bulk_category_and_one_off(db_path):
    conn = _history(db_path)
    ids = [r["id"] for r in conn.execute("SELECT id FROM transactions WHERE amount < 0 AND substr(date,1,7) = '2026-06'")]
    conn.close()
    client = TestClient(app)
    hx = {"HX-Request": "true", "HX-Target": "txn-results"}
    r = client.post("/transactions/bulk/category", headers=hx,
                    data={"ids": [str(i) for i in ids], "category": "Shopping / Gifts", "month": "2026-06"})
    assert r.status_code == 200 and r.text.lstrip().startswith('<div id="txn-results">')
    assert f"Filed {len(ids)} under Gifts" in r.headers["HX-Trigger"] and '"refresh": true' in r.headers["HX-Trigger"]
    assert "in Jun 2026" in r.text                                   # re-rendered under the posted filters
    r = client.post("/transactions/bulk/one_off", headers=hx, data={"ids": [str(i) for i in ids], "month": "2026-06"})
    assert f"Marked {len(ids)} one-off" in r.headers["HX-Trigger"]
    conn = db.connect(db_path)
    rows = conn.execute(f"SELECT one_off, category_id FROM transactions WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    gifts = db.category_id_by_path(conn, "Shopping/Gifts")
    assert all(r["one_off"] == 1 and r["category_id"] == gifts for r in rows)
    conn.close()
    r = client.post("/transactions/bulk/category", headers=hx, data={"category": "Shopping / Gifts"})
    assert "Select at least one transaction." in r.headers["HX-Trigger"]


def test_one_off_accepts_blank_as_off(db_path):
    conn = _history(db_path)
    txn = conn.execute("SELECT id FROM transactions LIMIT 1").fetchone()["id"]
    conn.close()
    client = TestClient(app)
    client.post(f"/transactions/{txn}/one_off", data={"one_off": "1"})
    r = client.post(f"/transactions/{txn}/one_off", data={"one_off": ""})
    assert "One-off removed" in r.headers["HX-Trigger"] and f'id="txn-{txn}-one-off" aria-label="One-off">' in r.text


def test_a_577_row_month_stays_under_300kb(db_path):
    conn = db.connect(db_path)
    add_rules(conn, RULES)
    names = ["Tesco Stores 2041 Exampletown GBR", "Sainsburys S/mkt Exampletown", "TFL TRAVEL CH\\EXAMPLE STREET",
             "Pret A Manger London GBR", "Amazon.co.uk*EX12AB34C", "Deliveroo Exampletown GBR", "Boots 0571 Exampletown"]
    txns = []
    for i in range(577):
        lines = ["UNKNOWN MERCHANT %d LONDON GBR" % i] if i % 6 == 0 else [names[i % len(names)]]
        txns.append(ParsedTransaction(date(2026, 6, 1 + i % 30), "CARD", lines, -round(3 + (i * 7.31) % 90, 2),
                                      external_id=f"tx{i}"))
    importer.import_statement(conn, _statement(txns, "2026-06-01", "2026-06-30", MONZO_ID, "Monzo Alex"),
                              "m.csv", "hash-m")
    conn.close()
    r = TestClient(app).get("/transactions?month=2026-06")
    assert r.text.count('<tr id="txn-') == 577
    assert len(r.content) < 300_000, f"{len(r.content) / 1024:.0f}KB"


# ------------------------------------------------------------- one-offs step
def _spike(db_path):
    """Groceries £100 a month Mar–May, then £500 in June: £100 as usual plus a £400 one-off."""
    conn = db.connect(db_path)
    add_rules(conn, RULES)
    txns = [ParsedTransaction(date(2026, mo, 3), ")))", ["TESCO STORES 2041"], -100.0) for mo in (3, 4, 5, 6)]
    txns.append(ParsedTransaction(date(2026, 6, 20), ")))", ["SAINSBURYS PARTY ORDER"], -400.0))
    importer.import_statement(conn, _statement(txns), "s.pdf", "hash-s")
    return conn


def test_one_offs_step_lists_each_flagged_category(db_path):
    conn = _spike(db_path)
    groceries = db.category_id_by_path(conn, "Groceries")
    step = next(s for s in review.checklist(conn, "2026-06", today=date(2026, 7, 5))["steps"] if s["key"] == "one_offs")
    assert step["done"] is False and step["count"] == "1 to check"
    assert step["href"] == "/review?month=2026-06#step-one_offs"        # the list, not just the first category
    spike = step["spikes"][0]
    assert spike["name"] == "Groceries" and spike["amount"] == 500 and spike["typical"] == 100
    assert spike["months"] == ["2026-03", "2026-04", "2026-05"] and spike["limit"] == 200
    # largest spend first, so the likely one-off is at the top
    assert spike["href"] == f"/transactions?month=2026-06&category_id={groceries}&sort=amount&dir=asc"
    conn.close()

    html = TestClient(app).get("/review?month=2026-06").text
    step_html = html[html.index('id="step-one_offs"'):html.index('id="step-balances"')]
    assert "Tick <b>One-off</b>" in step_html                           # says what to do
    assert "£500 in Jun" in step_html and "typical £100 (Mar–May)" in step_html
    assert f'href="/transactions?month=2026-06&amp;category_id={groceries}&amp;sort=amount&amp;dir=asc"' in step_html


def test_ticking_the_one_off_clears_the_step_and_the_totals_agree(db_path):
    conn = _spike(db_path)
    groceries = db.category_id_by_path(conn, "Groceries")
    party = conn.execute("SELECT id FROM transactions WHERE description LIKE 'SAINSBURYS PARTY%'").fetchone()["id"]
    conn.close()
    client = TestClient(app)
    page = client.get(f"/transactions?month=2026-06&category_id={groceries}&sort=amount&dir=asc").text
    assert "net −£500.00" in page and "without one-offs" not in page
    # the click-through says what it is filtered to: a top-level category is a filter option, not just its children
    assert f'<option value="{groceries}" selected>Groceries (all)</option>' in page
    assert "2 transactions in Groceries in Jun 2026" in page
    client.post(f"/transactions/{party}/one_off", data={"one_off": "1"})
    page = client.get(f"/transactions?month=2026-06&category_id={groceries}").text
    assert "net −£500.00 · −£100.00 without one-offs" in page           # the figure Review checks against typical
    conn = db.connect(db_path)
    step = next(s for s in review.checklist(conn, "2026-06", today=date(2026, 7, 5))["steps"] if s["key"] == "one_offs")
    assert step["done"] is True
    conn.close()
