"""Filing one row on a filtered Transactions page: a row that no longer matches the page's filters leaves the list and
the result line follows; the last one shows the all-filed state; Undo puts back exactly what was there; making a rule
reloads the page with its toast in the flash cookie."""
import json
from urllib.parse import unquote

from fastapi.testclient import TestClient

from slopfi import categorise, db, importer
from slopfi.web.app import app

from factories import add_rules, rule, statement, txn

UNCAT_JUNE = "?month=2026-06&uncategorised=1"


def _june(db_path) -> dict:
    """June on the joint account: Tesco filed by a rule, three shops nobody has filed. Returns ids by description."""
    conn = db.connect(db_path)
    add_rules(conn, [rule("TESCO", "Groceries/Supermarket")])
    importer.import_statement(conn, statement([
        txn("2026-06-03", "TESCO STORES 2041", -80.0),
        txn("2026-06-10", "CORNER SHOP", -12.0),
        txn("2026-06-11", "MARKET STALL", -8.0),
        txn("2026-06-12", "BARBER & CO", -18.0),
    ]), "june.pdf", "hash-june")
    ids = {r["description"].split(" / ")[0]: r["id"] for r in conn.execute("SELECT id, description FROM transactions")}
    conn.close()
    return ids


def _events(r) -> dict:
    return json.loads(r.headers["HX-Trigger"])


def _row(db_path, txn_id: int):
    conn = db.connect(db_path)
    row = conn.execute("SELECT category_id, categorised_by, rule_id FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    conn.close()
    return dict(row)


def test_a_filed_row_leaves_the_uncategorised_list_and_the_result_line_follows(db_path):
    ids = _june(db_path)
    r = TestClient(app).post(f"/transactions/{ids['CORNER SHOP']}/category",
                             data={"category": "Shopping / Gifts", "view": UNCAT_JUNE})
    assert r.headers["HX-Reswap"].startswith("delete")
    assert f'id="txn-{ids["CORNER SHOP"]}"' not in r.text
    assert 'id="result-line"' in r.text and 'hx-swap-oob="true"' in r.text
    assert "2 uncategorised in Jun 2026" in r.text
    toast = _events(r)["toast"]
    assert toast["message"] == "Filed under Gifts"
    assert toast["action"]["label"] == "Undo" and toast["action"]["post"] == f"/transactions/{ids['CORNER SHOP']}/undo"


def test_a_row_that_still_matches_stays_with_the_result_line(db_path):
    ids = _june(db_path)
    r = TestClient(app).post(f"/transactions/{ids['CORNER SHOP']}/category",
                             data={"category": "Shopping / Gifts", "view": "?month=2026-06"})
    assert "HX-Reswap" not in r.headers
    assert f'<tr id="txn-{ids["CORNER SHOP"]}"' in r.text and "4 transactions in Jun 2026" in r.text


def test_a_bare_page_view_means_the_month_being_closed(db_path):
    ids = _june(db_path)
    r = TestClient(app).post(f"/transactions/{ids['CORNER SHOP']}/category",
                             data={"category": "Shopping / Gifts", "view": "?"})
    assert "in Jun 2026" in r.text and f'<tr id="txn-{ids["CORNER SHOP"]}"' in r.text


def test_without_a_view_the_row_is_re_rendered_as_before(db_path):
    ids = _june(db_path)
    r = TestClient(app).post(f"/transactions/{ids['CORNER SHOP']}/category", data={"category": "Shopping / Gifts"})
    assert r.text.lstrip().startswith(f'<tr id="txn-{ids["CORNER SHOP"]}"') and "result-line" not in r.text


def test_filing_the_last_one_shows_all_filed(db_path):
    ids = _june(db_path)
    client = TestClient(app)
    for name in ("CORNER SHOP", "MARKET STALL"):
        client.post(f"/transactions/{ids[name]}/category", data={"category": "Shopping / Gifts", "view": UNCAT_JUNE})
    r = client.post(f"/transactions/{ids['BARBER & CO']}/category", data={"category": "Shopping / Gifts", "view": UNCAT_JUNE})
    assert r.headers["HX-Retarget"] == "#txn-results" and r.headers["HX-Reswap"] == "outerHTML"
    assert "All filed for Jun 2026" in r.text and "Nothing left to categorise here." in r.text
    assert "No uncategorised transactions in Jun 2026" in r.text
    # a search that finds nothing uncategorised is still "no match", not "all filed"
    r = client.get("/transactions?month=2026-06&uncategorised=1&q=zzz", headers={"HX-Request": "true", "HX-Target": "txn-results"})
    assert "No transactions match" in r.text and "All filed" not in r.text


def test_undo_puts_back_exactly_what_was_there(db_path):
    ids = _june(db_path)
    client = TestClient(app)
    tesco = ids["TESCO STORES 2041"]
    before = _row(db_path, tesco)
    assert before["categorised_by"] == "rule" and before["rule_id"]
    r = client.post(f"/transactions/{tesco}/category", data={"category": "Shopping / Gifts", "view": UNCAT_JUNE})
    assert _row(db_path, tesco)["categorised_by"] == "manual"
    undo = _events(r)["toast"]["action"]
    r = client.post(undo["post"], data={**undo["values"], "view": UNCAT_JUNE})
    assert _row(db_path, tesco) == before
    assert _events(r)["toast"]["message"] == "Back under Supermarket"
    assert r.headers["HX-Retarget"] == "#txn-results" and r.text.lstrip().startswith('<div id="txn-results">')
    # an unfiled row goes back to Uncategorised and reappears in the filtered list
    shop = ids["CORNER SHOP"]
    r = client.post(f"/transactions/{shop}/category", data={"category": "Shopping / Gifts", "view": UNCAT_JUNE})
    undo = _events(r)["toast"]["action"]
    r = client.post(undo["post"], data={**undo["values"], "view": UNCAT_JUNE})
    assert _row(db_path, shop) == {"category_id": None, "categorised_by": None, "rule_id": None}
    assert _events(r)["toast"]["message"] == "Back to Uncategorised" and f'id="txn-{shop}"' in r.text


def test_restore_drops_a_rule_deleted_since(db_path):
    ids = _june(db_path)
    conn = db.connect(db_path)
    tesco = ids["TESCO STORES 2041"]
    before = _row(db_path, tesco)
    categorise.set_category(conn, tesco, None)
    conn.execute("UPDATE transactions SET rule_id = NULL")
    conn.execute("DELETE FROM rules")
    categorise.restore_category(conn, tesco, before["category_id"], "rule", before["rule_id"])
    conn.close()
    assert _row(db_path, tesco) == {"category_id": before["category_id"], "categorised_by": "manual", "rule_id": None}


def test_making_a_rule_reloads_with_the_toast_in_the_flash_cookie(db_path):
    ids = _june(db_path)
    conn = db.connect(db_path)
    importer.import_statement(conn, statement([txn("2026-07-10", "CORNER SHOP", -9.0)], "2026-07-01", "2026-07-31"),
                              "july.pdf", "hash-july")
    conn.close()
    r = TestClient(app).post(f"/transactions/{ids['CORNER SHOP']}/category", data={
        "category": "Shopping / Gifts", "create_rule": "1", "pattern": "CORNER SHOP", "view": UNCAT_JUNE})
    assert r.headers["HX-Refresh"] == "true" and "HX-Trigger" not in r.headers
    assert "Filed under Gifts · rule made, 1 more filed" in json.loads(unquote(r.cookies["flash"]))["message"]


def test_a_rule_that_files_nothing_else_says_so_without_undo(db_path):
    ids = _june(db_path)
    r = TestClient(app).post(f"/transactions/{ids['CORNER SHOP']}/category", data={
        "category": "Shopping / Gifts", "create_rule": "1", "pattern": "CORNER SHOP", "view": UNCAT_JUNE})
    toast = _events(r)["toast"]
    assert toast["message"] == "Filed under Gifts · rule made" and "action" not in toast
