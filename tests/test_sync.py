from slopfi import sync as sync_mod

import factories
from test_monzo_parser import HEADER, ROWS


def test_sync_from_config(conn, tmp_path):
    (tmp_path / "alex").mkdir()
    (tmp_path / "alex" / "Monzo_a.csv").write_text(HEADER + ROWS, encoding="utf-8")
    (tmp_path / "alex" / "Monzo_b.csv").write_text(HEADER + ROWS.splitlines(keepends=True)[0], encoding="utf-8")
    (tmp_path / "empty").mkdir()
    cfg = tmp_path / "sources.toml"
    cfg.write_text('''
[[source]]
path = "alex"
account = "Monzo Alex"
owner = "alex"

[[source]]
path = "empty"
account = "Nothing"

[[source]]
path = "missing"
account = "Nothing"
''')
    sources = sync_mod.load_sources(cfg)
    assert [s.account for s in sources] == ["Monzo Alex", "Nothing", "Nothing"]
    results = sync_mod.sync(conn, sources)
    by_status = [(r.status, r.inserted, r.skipped) for r in results]
    assert by_status[0] == ("imported", 6, 0)
    assert by_status[1] == ("imported", 0, 1)        # overlapping export: nothing new
    assert by_status[2][0] == "error" and by_status[3][0] == "error"
    acct = conn.execute("SELECT name, owner, kind FROM accounts").fetchall()
    assert [tuple(a) for a in acct] == [("Monzo Alex", "alex", "current")]

    # second sync is a no-op
    again = sync_mod.sync(conn, sources[:1])
    assert all(r.status == "duplicate" for r in again)


def test_pdf_account_identifier_attaches_and_mismatch_rejected(conn):
    from slopfi import importer

    def stmt(identifier):
        return factories.statement([factories.txn("2026-06-02", "EXAMPLE WATER", -5.0, "DD")], identifier=identifier,
                                   opening=0, closing=-5, payments_in=0, payments_out=5)

    acct = importer.get_or_create_account(conn, "HSBC Joint", "joint", "current")
    r = importer.import_statement(conn, stmt("99-10-20 12341020"), "a.pdf", "h1", account_id=acct)
    assert r.status == "imported"
    assert conn.execute("SELECT identifier FROM accounts WHERE id = ?", (acct,)).fetchone()[0] == "99-10-20 12341020"
    import pytest
    with pytest.raises(ValueError):
        importer.import_statement(conn, stmt("99-10-20 12342020"), "b.pdf", "h2", account_id=acct)
