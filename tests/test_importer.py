from slopfi import importer, reports

from factories import JOINT_STATEMENT_RULES, joint_statement


def test_import_is_idempotent_and_categorises(conn, make_rules):
    make_rules(conn, JOINT_STATEMENT_RULES)
    r1 = importer.import_statement(conn, joint_statement(), "a.pdf", "hash1")
    assert r1.status == "imported" and r1.inserted == 5 and r1.skipped == 0
    assert r1.categorised == 3  # transfer, supermarket, fx fee
    acct = conn.execute("SELECT * FROM accounts").fetchone()
    assert acct["owner"] == "joint" and acct["kind"] == "current"

    # overlapping statement with the same transactions: nothing duplicated
    r2 = importer.import_statement(conn, joint_statement(("2026-06-20", "2026-07-19")), "b.pdf", "hash2")
    assert r2.inserted == 0 and r2.skipped == 5
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 5

    summary = reports.monthly_summary(conn)
    june = next(m for m in summary if m["month"] == "2026-06")
    assert june["transfers_in"] == 1350.0 and june["income"] == 0
    assert abs(june["outgoings"] - 116.40) < 0.005 and june["uncategorised_count"] == 2

    breakdown = reports.category_breakdown(conn, "2026-06")
    names = {b["name"]: b["spend"] for b in breakdown}
    assert names["Groceries"] == 96.40 and names["Uncategorised"] == 20.0

    importer.delete_statement(conn, r1.statement_id)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_without_rules_nothing_is_filed(conn):
    r = importer.import_statement(conn, joint_statement(), "a.pdf", "hash1")
    assert r.inserted == 5 and r.categorised == 0
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE category_id IS NOT NULL").fetchone()[0] == 0
