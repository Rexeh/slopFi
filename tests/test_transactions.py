"""`with conn:` is one transaction, including when an inner block sits inside an outer one."""
import pytest

from slopfi import categorise, db, importer
from factories import joint_statement


def test_a_failed_import_leaves_nothing_behind_and_can_be_retried(conn, monkeypatch):
    def boom(*a, **k):                                                              # after every row is inserted
        raise RuntimeError("crash")

    with monkeypatch.context() as m:
        m.setattr(categorise, "apply_rules", boom)
        with pytest.raises(RuntimeError):
            importer.import_statement(conn, joint_statement(), "a.pdf", "hash1")
    for table in ("accounts", "statements", "transactions"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert importer.import_statement(conn, joint_statement(), "a.pdf", "hash1").inserted == 5


def test_an_inner_block_joins_the_outer_transaction(conn):
    with pytest.raises(RuntimeError):
        with conn:
            db.set_setting(conn, "k", "inner")                                       # has its own `with conn`
            assert conn.in_transaction                                               # the inner block did not commit
            raise RuntimeError("crash")
    assert db.get_setting(conn, "k") is None and not conn.in_transaction
    with conn:
        db.set_setting(conn, "k", "kept")
    assert db.get_setting(conn, "k") == "kept" and not conn.in_transaction


def test_a_file_someone_else_imports_mid_import_is_a_duplicate_not_a_crash(conn, monkeypatch, tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"statement")

    def other_person_finishes_first(_):                                              # after import_file's duplicate check
        importer.import_statement(conn, joint_statement(), "a.pdf", importer.sha256_file(path))
        return [joint_statement()]

    monkeypatch.setattr(importer, "parse_file_all", other_person_finishes_first)
    assert [r.status for r in importer.import_file(conn, path)] == ["duplicate"]
    assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 5
