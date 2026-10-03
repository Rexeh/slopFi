"""Shared fixtures. No test reads the owner's own files: statements come from the demo generator and every
other value is a fake from tests/factories.py."""
import pytest

from slopfi import db

import factories


@pytest.fixture(scope="session", autouse=True)
def _away_from_the_owners_files(tmp_path_factory):
    """The app defaults to ./slopfi.db and ./sources.toml, which in a checkout are the owner's own files. Point
    both at an empty directory for the whole session; db_path and the demo fixtures override them per test."""
    home = tmp_path_factory.mktemp("isolated")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SLOPFI_DB", str(home / "slopfi.db"))
        mp.setenv("SLOPFI_SOURCES", str(home / "sources.toml"))
        yield


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("SLOPFI_DB", str(path))
    return path


@pytest.fixture
def make_rules():
    """Install explicit rules on a connection: make_rules(conn, [rule("TESCO", "Groceries/Supermarket"), ...]).
    A fresh database has none, so a test declares every rule it relies on."""
    return factories.add_rules


@pytest.fixture(scope="session")
def demo_statements(tmp_path_factory) -> factories.DemoStatements:
    """The demo household's statements (12 HSBC PDFs, 12 Amex PDFs, one Monzo PDF with two pots and one Monzo
    CSV), written once per test session into a temporary directory."""
    return factories.write_demo_statements(tmp_path_factory.mktemp("demo-statements"))
