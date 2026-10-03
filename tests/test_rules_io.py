"""Rules are not seeded; they move in and out as JSON (CLI, sync's rules_file, and the Rules tab)."""
from __future__ import annotations

import json
from urllib.parse import unquote

from fastapi.testclient import TestClient

from slopfi import categorise, cli, db, importer, rules_io
from slopfi.web.app import app

CSV_HEADER = ("Transaction ID,Date,Time,Type,Name,Emoji,Category,Amount,Currency,Local amount,Local currency,"
              "Notes and #tags,Address,Receipt,Description,Category split,Money Out,Money In\n")
CSV_ROWS = ("tx_1,03/06/2026,09:00:00,Card payment,Corner Bakery,,General,-4.50,GBP,-4.50,GBP,,,,CORNER BAKERY,,-4.50,\n"
            "tx_2,04/06/2026,09:00:00,Card payment,Hilltop Farm,,General,-12.00,GBP,-12.00,GBP,,,,HILLTOP FARM,,-12.00,\n")

SAMPLE = [
    {"pattern": "CORNER BAKERY", "match_type": "contains", "category": "Eating out/Restaurants & cafes", "priority": 40},
    {"pattern": "^HILLTOP", "match_type": "regex", "category": "Groceries/Farm shops", "priority": 50},
    {"pattern": "COUNCIL", "match_type": "prefix", "category": "Housing/Council tax", "priority": 10, "type_code": "DD",
     "amount_min": -500.0, "amount_max": -50.0},
    {"pattern": "OLD RULE", "match_type": "contains", "category": "Other spending", "priority": 90, "enabled": False},
]


def _flash(response) -> dict:
    raw = response.cookies.get("flash") or response.headers.get("set-cookie", "").split("flash=")[1].split(";")[0]
    return json.loads(unquote(raw.strip('"')))


# ------------------------------------------------------------ no seeding
def test_a_fresh_database_has_categories_but_no_rules(conn):
    assert conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] > 50
    assert db.category_id_by_path(conn, "Groceries/Supermarket")


def test_reopening_an_existing_database_keeps_its_rules(db_path):
    conn = db.connect(db_path)
    categorise.create_rule(conn, "TESCO", db.category_id_by_path(conn, "Groceries/Supermarket"))
    with conn:
        conn.execute("UPDATE rules SET source = 'seed'")                  # as an older build seeded it
    conn.close()
    conn = db.connect(db_path)
    assert [(r["pattern"], r["source"]) for r in conn.execute("SELECT * FROM rules")] == [("TESCO", "seed")]
    conn.close()


# ----------------------------------------------------------- round trip
def test_export_then_import_round_trips(conn, tmp_path):
    summary = rules_io.import_rules(conn, SAMPLE)
    assert summary.added == 4 and summary.categories_created == ["Groceries/Farm shops"] and not summary.errors
    path = tmp_path / "rules.json"
    assert rules_io.export_file(conn, path) == 4
    exported = json.loads(path.read_text())
    assert exported[0] == {"pattern": "COUNCIL", "match_type": "prefix", "category": "Housing/Council tax",
                           "priority": 10, "type_code": "DD", "amount_min": -500.0, "amount_max": -50.0, "enabled": True}
    assert exported[-1]["enabled"] is False and "type_code" not in exported[-1]

    fresh = db.connect(":memory:")
    again = rules_io.import_file(fresh, path)
    assert again.added == 4 and again.categories_created == ["Groceries/Farm shops"]
    assert rules_io.export_rules(fresh) == exported
    fresh.close()


def test_import_skips_duplicates_and_unknown_parents_and_bad_rules(conn):
    rules_io.import_rules(conn, SAMPLE)
    summary = rules_io.import_rules(conn, SAMPLE + [
        {"pattern": "X", "category": "Nonexistent/Child"},
        {"pattern": "(", "match_type": "regex", "category": "Other spending"},
        {"pattern": "Y", "match_type": "fuzzy", "category": "Other spending"},
        {"pattern": "", "category": "Other spending"},
        {"pattern": "Z", "category": "Other spending", "priority": "high"},
    ])
    assert summary.added == 0 and summary.duplicates == 4 and len(summary.errors) == 5
    assert "unknown category 'Nonexistent/Child'" in " ".join(summary.errors)
    assert db.category_id_by_path(conn, "Nonexistent") is None
    assert conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 4
    assert rules_io.describe(summary) == "0 rules imported, 4 duplicates skipped, 5 rules not imported"


def test_replace_deletes_existing_rules_first(conn):
    categorise.create_rule(conn, "MANUAL ONE", db.category_id_by_path(conn, "Shopping/Online"))
    summary = rules_io.import_rules(conn, SAMPLE[:1], replace=True)
    assert summary.removed == 1 and summary.added == 1
    assert [r["pattern"] for r in conn.execute("SELECT pattern FROM rules")] == ["CORNER BAKERY"]


def test_parse_rejects_files_that_are_not_rule_lists():
    for text in ("not json", '{"pattern": "x"}', "[1, 2]"):
        try:
            rules_io.parse(text)
        except ValueError:
            continue
        raise AssertionError(text)


# ----------------------------------------------------------------- CLI
def test_cli_export_and_import(db_path, tmp_path, capsys):
    src = tmp_path / "in.json"
    src.write_text(json.dumps(SAMPLE))
    assert cli.main(["rules", "import", str(src)]) == 0
    assert "4 rules imported" in capsys.readouterr().out
    assert cli.main(["rules", "import", str(src)]) == 0
    assert "0 rules imported, 4 duplicates skipped" in capsys.readouterr().out
    out = tmp_path / "out.json"
    assert cli.main(["rules", "export", str(out)]) == 0
    assert len(json.loads(out.read_text())) == 4
    assert cli.main(["rules", "import", "--replace", str(tmp_path / "missing.json")]) == 1


def test_sync_applies_the_rules_file_from_sources_toml(db_path, tmp_path, capsys):
    (tmp_path / "sam").mkdir()
    (tmp_path / "sam" / "export.csv").write_text(CSV_HEADER + CSV_ROWS, encoding="utf-8")
    (tmp_path / "my-rules.json").write_text(json.dumps(SAMPLE[:2]))
    config = tmp_path / "sources.toml"
    config.write_text('rules_file = "my-rules.json"\n\n[[source]]\npath = "sam"\naccount = "Monzo Sam"\nowner = "sam"\n')
    assert cli.main(["sync", "--config", str(config)]) == 0
    assert "my-rules.json: 2 rules imported" in capsys.readouterr().out
    conn = db.connect(db_path)
    cats = dict(conn.execute("""SELECT t.description, c.name FROM transactions t
                                JOIN categories c ON c.id = t.category_id""").fetchall())
    assert cats == {"Corner Bakery": "Restaurants & cafes", "Hilltop Farm": "Farm shops"}
    conn.close()
    assert cli.main(["sync", "--config", str(config)]) == 0          # re-running adds nothing twice
    assert "0 rules imported, 2 duplicates skipped" in capsys.readouterr().out


def test_sync_without_a_rules_file_leaves_rules_alone(db_path, tmp_path):
    (tmp_path / "sam").mkdir()
    (tmp_path / "sam" / "export.csv").write_text(CSV_HEADER + CSV_ROWS, encoding="utf-8")
    config = tmp_path / "sources.toml"
    config.write_text('[[source]]\npath = "sam"\naccount = "Monzo Sam"\nowner = "sam"\n')
    assert cli.main(["sync", "--config", str(config)]) == 0
    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 0
    conn.close()


# ----------------------------------------------------------------- web
def test_rules_tab_offers_export_and_import(db_path):
    page = TestClient(app).get("/categories").text
    assert 'href="/rules/export"' in page and ">Export rules</a>" in page and 'data-dialog="#import-rules"' in page
    assert '<dialog class="form-dialog" id="import-rules"' in page and 'type="file" name="file"' in page
    assert 'data-confirm="Import rules from this file?"' in page and 'name="replace" value="1"' in page


def test_web_export_downloads_json(db_path):
    conn = db.connect(db_path)
    rules_io.import_rules(conn, SAMPLE)
    conn.close()
    r = TestClient(app).get("/rules/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert 'attachment; filename="slopfi-rules.json"' == r.headers["content-disposition"]
    assert [x["pattern"] for x in r.json()] == ["COUNCIL", "CORNER BAKERY", "^HILLTOP", "OLD RULE"]


def test_web_import_files_transactions_and_toasts(db_path):
    conn = db.connect(db_path)
    acct = importer.get_or_create_account(conn, "Monzo Sam", "sam", "current")
    conn.close()
    client = TestClient(app)
    path = db_path.parent / "export.csv"
    path.write_text(CSV_HEADER + CSV_ROWS, encoding="utf-8")
    conn = db.connect(db_path)
    importer.import_file(conn, path, account_id=acct)
    conn.close()

    body = json.dumps(SAMPLE).encode()
    r = client.post("/rules/import", files={"file": ("rules.json", body, "application/json")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/categories#rules"
    flash = _flash(r)
    assert flash["kind"] == "success"
    assert flash["message"] == "4 rules imported, 1 category created, 2 transactions filed"

    r = client.post("/rules/import", files={"file": ("rules.json", body, "application/json")},
                    data={"replace": "1"}, follow_redirects=False)
    assert _flash(r)["message"].startswith("4 rules imported, 4 old rules replaced")

    r = client.post("/rules/import", files={"file": ("notes.txt", b"hello", "text/plain")}, follow_redirects=False)
    assert _flash(r)["kind"] == "error" and "is not a rules file" in _flash(r)["message"]
    page = client.get("/categories").text
    assert ">Imported<" in page
