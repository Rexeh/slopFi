"""Merging an account the bank renamed: a Monzo pot's identity is its name, so a rename makes a second account."""
import sqlite3
from datetime import date
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from slopfi import db, importer, projection, reports, review
from slopfi.web.app import app
from factories import MONZO_ID, joint_statement, statement, txn

OLD, NEW = 1, 2


def _pot(name: str, start: str, end: str, closing: float, hash_: str, conn) -> None:
    stmt = statement([txn(start, "Transfer from Personal Account", 100.0, "POT", balance_after=closing)], start, end,
                     identifier=f"{MONZO_ID} pot:{name}", name=f"Monzo pot: {name}", kind="savings", institution="Monzo",
                     parser="monzo_pdf", opening=closing - 100.0, closing=closing)
    importer.import_statement(conn, stmt, f"{hash_}.pdf", hash_)


def _renamed(conn) -> None:
    _pot("Fund", "2026-09-01", "2026-09-30", 500.0, "sep", conn)
    _pot("FundNew", "2026-10-01", "2026-10-31", 600.0, "oct", conn)


def test_merge_moves_everything_and_clears_the_stale_old_pot(conn):
    _renamed(conn)
    today = date(2026, 11, 10)
    assert [a["id"] for a in review._stale_accounts(conn, today)] == [OLD]
    assert reports.net_worth(conn)["cash_in_accounts"] == 1100.0                   # the money, counted twice
    for acct, balance in ((OLD, 1.0), (NEW, 2.0)):
        conn.execute("INSERT INTO balance_snapshots (account_id, date, balance) VALUES (?, '2026-08-01', ?)", (acct, balance))
    conn.execute("INSERT INTO balance_snapshots (account_id, date, balance) VALUES (?, '2026-08-02', 3.0)", (OLD,))
    sid = projection.ensure_default_scenario(conn)
    conn.execute("INSERT INTO scenario_items (scenario_id, ref_type, ref_id, annual_growth_pct) VALUES (?, 'account', ?, 4.0)", (sid, OLD))
    conn.execute("UPDATE scenarios SET surplus_target = ? WHERE id = ?", (f"account:{OLD}", sid))

    assert importer.merge_accounts(conn, OLD, NEW) == ("Monzo savings …Fund", "Monzo savings …dNew")

    assert [r["id"] for r in conn.execute("SELECT id FROM accounts")] == [NEW]
    for table in ("statements", "transactions"):
        assert {r[0] for r in conn.execute(f"SELECT account_id FROM {table}")} == {NEW}
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    snaps = {r["date"]: r["balance"] for r in conn.execute("SELECT date, balance FROM balance_snapshots WHERE account_id = ?", (NEW,))}
    assert snaps == {"2026-08-01": 2.0, "2026-08-02": 3.0}                         # a clash keeps the surviving account's
    assert conn.execute("SELECT ref_id FROM scenario_items WHERE ref_type = 'account'").fetchone()[0] == NEW
    assert conn.execute("SELECT surplus_target FROM scenarios WHERE id = ?", (sid,)).fetchone()[0] == f"account:{NEW}"
    assert review._stale_accounts(conn, today) == []
    assert reports.net_worth(conn)["cash_in_accounts"] == 600.0

    _pot("FundNew", "2026-11-01", "2026-11-30", 700.0, "nov", conn)               # the new name still finds the account
    assert [r["id"] for r in conn.execute("SELECT id FROM accounts")] == [NEW]


def test_merge_refuses_overlapping_statements_and_changes_nothing(conn):
    _pot("Fund", "2026-09-01", "2026-10-02", 500.0, "sep", conn)
    _pot("FundNew", "2026-10-02", "2026-10-31", 600.0, "oct", conn)
    with pytest.raises(ValueError, match="sep.pdf and oct.pdf cover the same dates"):
        importer.merge_accounts(conn, OLD, NEW)
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2


def test_merge_refuses_different_accounts(conn):
    _pot("Fund", "2026-09-01", "2026-09-30", 500.0, "sep", conn)
    importer.import_statement(conn, joint_statement(), "joint.pdf", "joint")
    with pytest.raises(ValueError, match="not the same kind"):
        importer.merge_accounts(conn, OLD, NEW)
    with pytest.raises(ValueError, match="two different"):
        importer.merge_accounts(conn, OLD, OLD)
    with pytest.raises(ValueError, match="two different"):
        importer.merge_accounts(conn, OLD, 99)


def test_merge_is_all_or_nothing(conn):
    _renamed(conn)
    conn.execute("CREATE TRIGGER fail BEFORE DELETE ON accounts BEGIN SELECT RAISE(ABORT, 'no'); END")
    with pytest.raises(sqlite3.IntegrityError):
        importer.merge_accounts(conn, OLD, NEW)
    assert conn.execute("SELECT account_id FROM statements ORDER BY id").fetchall()[0][0] == OLD


def test_merge_from_the_accounts_tab(db_path):
    conn = db.connect(db_path)
    _renamed(conn)
    importer.import_statement(conn, joint_statement(), "joint.pdf", "joint")
    conn.close()
    client = TestClient(app)
    page = client.get("/statements").text
    assert f'action="/accounts/{OLD}/merge"' in page and 'aria-label="Merge Monzo savings …Fund into"' in page
    assert f'<option value="{NEW}">Monzo savings …dNew</option>' in page
    assert 'action="/accounts/3/merge"' not in page                                # the current account has nothing to merge with

    r = client.post(f"/accounts/{OLD}/merge", data={"into_id": "3"}, follow_redirects=False)
    assert r.status_code == 303 and "Not merged" in unquote(r.headers["set-cookie"])
    r = client.post(f"/accounts/{OLD}/merge", data={"into_id": str(NEW)}, follow_redirects=False)
    assert r.status_code == 303 and "merged into" in unquote(r.headers["set-cookie"])
    assert db.connect(db_path).execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2
