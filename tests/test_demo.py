"""The demo household: generated statements parse and reconcile, the build fills every page, and it is
deterministic for a fixed seed."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from slopfi import cli, db, household, review
from slopfi.demo import build as demo_build
from slopfi.demo import household as demo_household
from slopfi.parsers import parse_file_all

REFERENCE = date(2026, 10, 3)
PAGES = ["/", "/transactions", "/spending", "/networth", "/projection", "/review", "/statements", "/categories",
         "/settings"]


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    return demo_build.build(tmp_path_factory.mktemp("demo"), seed=42, reference=REFERENCE)


@pytest.fixture
def client(demo, monkeypatch):
    monkeypatch.setenv("SLOPFI_DB", str(demo.db_path))
    monkeypatch.setenv("SLOPFI_SOURCES", str(demo.out / "sources.toml"))
    from slopfi.web.app import app

    return TestClient(app)


def _digest(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


# ------------------------------------------------------------------ build
def test_build_writes_every_format_quickly(demo):
    assert demo.seconds < 20
    names = sorted(p.relative_to(demo.out / "statements").as_posix() for p in demo.files)
    assert len([n for n in names if n.startswith("joint/hsbc/")]) == 12
    assert len([n for n in names if n.startswith("alex/amex/")]) == 12
    assert [n for n in names if n.startswith("alex/monzo/")] == ["alex/monzo/monzo-alex-2025-10-to-2026-09.pdf"]
    assert [n for n in names if n.startswith("sam/monzo/")] == ["sam/monzo/monzo-sam-export-2026-09.csv"]
    assert demo.months[0] == "2025-10" and demo.months[-1] == "2026-09" and len(demo.months) == 12
    assert (demo.out / "sources.toml").exists() and (demo.out / "rules.json").exists()


def test_month_window_is_the_twelve_complete_months_before_the_reference():
    assert demo_household.month_window(date(2026, 1, 15)) == [(2025, m) for m in range(1, 13)]
    assert demo_household.month_window(date(2026, 3, 1))[-1] == (2026, 2)


def test_every_generated_statement_parses_and_reconciles(demo):
    parsers = set()
    for path in demo.files:
        stmts = parse_file_all(path)
        for s in stmts:
            parsers.add(s.parser)
            assert s.reconcile() == [], (path.name, s.reconcile())
            assert s.warnings == [], (path.name, s.warnings)
            assert s.transactions, path.name
    assert parsers == {"hsbc_current", "amex_card", "monzo_pdf", "monzo_csv"}


def test_statements_carry_fake_identifiers_and_chain_month_to_month(demo):
    hsbc = [parse_file_all(p)[0] for p in demo.files if "hsbc" in p.name]
    amex = [parse_file_all(p)[0] for p in demo.files if "amex" in p.name]
    monzo = parse_file_all(next(p for p in demo.files if p.suffix == ".pdf" and "monzo" in p.name))
    assert {s.account_identifier for s in hsbc} == {"99-10-20 12341020"}
    assert {s.account_identifier for s in amex} == {"…00000"}
    assert [s.account_identifier for s in monzo] == ["99-30-40 12343040", "99-30-40 12343040 pot:Savings",
                                                    "99-30-40 12343040 pot:Rainy day"]
    assert [s.sub_account for s in monzo] == [None, "Savings", "Rainy day"]
    for stmts in (hsbc, amex):
        for before, after in zip(stmts, stmts[1:]):
            assert before.closing_balance == pytest.approx(after.opening_balance)
            assert (after.period_start - before.period_end).days == 1
    # holidays abroad: foreign-currency card payments with their rate, and the fee as its own line on HSBC
    assert any(t.fx_currency == "EUR" and t.fx_rate for s in hsbc for t in s.transactions)
    assert any("NON-STERLING" in t.description for s in hsbc for t in s.transactions)
    assert any(t.fx_rate and t.fx_amount == 980.0 for s in amex for t in s.transactions)
    assert any(t.fx_currency == "EUR" for t in monzo[0].transactions)
    assert any(t.type_code == "CR" for s in amex for t in s.transactions if "PAYMENT" not in t.description)  # refunds


def test_nothing_real_in_the_household():
    hh = demo_household.generate(42, REFERENCE)
    for acct in hh.accounts.values():
        assert not acct.sort_code or acct.sort_code.startswith("99-")
        assert acct.number.startswith("1234") or acct.number.endswith("00000")
    assert hh.name == "The Rivera household" and [p["name"] for p in hh.people] == ["Alex", "Sam"]
    lines = " ".join(" ".join(t.lines) for a in hh.accounts.values() for t in a.txns)
    assert "ACME ANALYTICS LTD" in lines


def test_database_is_dressed_for_every_page(demo):
    conn = db.connect(demo.db_path)
    try:
        accounts = {r["name"]: (r["owner"], r["kind"]) for r in conn.execute("SELECT * FROM accounts")}
        assert accounts == {"HSBC Joint": ("joint", "current"), "Monzo Alex": ("alex", "current"),
                            "Monzo pot: Savings": ("alex", "savings"), "Monzo pot: Rainy day": ("alex", "savings"),
                            "Monzo Sam": ("sam", "current"), "Amex Alex": ("alex", "credit_card")}
        # every demo rule is present; one identical to a built-in rule is kept once, as the built-in
        assert {r["pattern"].strip() for r in demo_build.DEMO_RULES} <= {r[0] for r in conn.execute("SELECT pattern FROM rules")}
        assert conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == demo.rules
        assert 35 <= len(demo_build.DEMO_RULES) <= 55
        # rules cover most merchants but leave work for Review, in the open month too
        total, uncategorised = conn.execute("SELECT COUNT(*), SUM(category_id IS NULL) FROM transactions").fetchone()
        assert total > 1000 and 0 < uncategorised < total * 0.15
        open_uncat = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_id IS NULL AND date LIKE '2026-09%'")
        assert open_uncat.fetchone()[0] > 0
        kinds = sorted(r["kind"] for r in conn.execute("SELECT kind FROM holdings"))
        assert kinds == ["cash_isa", "mortgage", "pension", "property", "stocks_isa"]
        assert conn.execute("SELECT COUNT(*) FROM holdings h JOIN holdings p ON p.id = h.parent_id "
                            "WHERE h.kind = 'mortgage' AND p.kind = 'property'").fetchone()[0] == 1
        assert [r[0] for r in conn.execute("SELECT DISTINCT date FROM balance_snapshots")] == ["2026-06-30"]
        assert conn.execute("SELECT COUNT(*) FROM budgets").fetchone()[0] == 3
        one_off = conn.execute("SELECT description, amount FROM transactions WHERE one_off = 1").fetchall()
        assert [(r[0], r[1]) for r in one_off] == [("MOTORCARE EXAMPLETOWN", -1236.40)]
        assert review.closed_months(conn) == list(reversed(demo.months[:-1]))
        assert review.latest_open_month(conn, REFERENCE) == "2026-09"
        plan = conn.execute("SELECT * FROM scenarios WHERE name = 'Plan'").fetchone()
        assert len(json.loads(plan["category_savings"])) == 2 and plan["surplus_target"].startswith("holding:")
        assert household.name(conn) == "The Rivera household"
        assert household.subtitle(conn) == "Household · Alex and Sam"
        # salaries, pot interest and seasonal spikes made it through
        cats = dict(conn.execute("""SELECT c.name, COUNT(*) FROM transactions t JOIN categories c ON c.id = t.category_id
                                    GROUP BY c.name""").fetchall())
        for name in ("Salary", "Interest received", "Mortgage", "Mortgage overpayment", "Council tax", "Energy",
                     "Supermarket", "Childcare", "Pet insurance", "Holidays", "FX fees", "Credit card payment",
                     "Savings", "Between own accounts", "Refunds"):
            assert cats.get(name), name
        by_month = dict(conn.execute("""SELECT substr(date, 1, 7), -SUM(amount) FROM transactions t
                                        JOIN categories c ON c.id = t.category_id
                                        WHERE c.kind = 'expense' GROUP BY 1""").fetchall())
        typical = sorted(by_month.values())[len(by_month) // 2]
        assert by_month["2025-12"] > typical and by_month["2026-08"] > typical
    finally:
        conn.close()


def test_every_page_shows_demo_data(client):
    expect = {"/": "Household · Alex and Sam", "/transactions": "TESCO STORES", "/spending": "Groceries",
              "/networth": "1 Example Street", "/projection": "Plan", "/review": "Categorise",
              "/statements": "hsbc-joint-2026-09.pdf", "/categories": "ACME ANALYTICS", "/settings": "Sam"}
    for path in PAGES:
        r = client.get(path)
        assert r.status_code == 200, path
        assert expect[path] in r.text, path
        assert "No rules yet" not in r.text and "Nothing imported yet" not in r.text, path
    assert "MOTORCARE" in client.get("/transactions?month=2026-02").text
    assert "Monzo Sam" in client.get("/networth").text


def test_build_is_deterministic_for_a_seed(demo, tmp_path):
    again = demo_build.build(tmp_path / "again", seed=42, reference=REFERENCE)
    assert _digest(again.out / "statements") == _digest(demo.out / "statements")
    assert (again.out / "rules.json").read_text() == (demo.out / "rules.json").read_text()

    def rows(path):
        conn = db.connect(path)
        try:
            return conn.execute("""SELECT a.name, t.date, t.description, t.amount, t.category_id, t.one_off
                                   FROM transactions t JOIN accounts a ON a.id = t.account_id
                                   ORDER BY a.name, t.date, t.seq""").fetchall()
        finally:
            conn.close()

    assert [tuple(r) for r in rows(again.db_path)] == [tuple(r) for r in rows(demo.db_path)]
    other = demo_household.generate(7, REFERENCE)
    same = demo_household.generate(42, REFERENCE)
    assert [t.pence for t in other.accounts["joint-hsbc"].txns] != [t.pence for t in same.accounts["joint-hsbc"].txns]


def test_build_refuses_to_overwrite_without_force(demo):
    with pytest.raises(FileExistsError):
        demo_build.build(demo.out, seed=42, reference=REFERENCE)


def test_serve_demo_points_the_app_at_the_demo(demo, monkeypatch):
    import uvicorn

    calls = {}
    monkeypatch.setenv("SLOPFI_DB", "elsewhere.db")          # restored after the test; serve --demo overrides it
    monkeypatch.delenv("SLOPFI_SOURCES", raising=False)
    monkeypatch.setattr(demo_build, "ensure", lambda out: calls.setdefault("db", demo.db_path))
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.update(app=a[0], port=k["port"]))
    assert cli.main(["serve", "--demo", "--port", "8799"]) == 0
    assert calls == {"db": demo.db_path, "app": "slopfi.web.app:app", "port": 8799}
    assert os.environ["SLOPFI_DB"] == str(demo.db_path)
    assert os.environ["SLOPFI_SOURCES"].endswith("sources.toml")
