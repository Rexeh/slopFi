"""Monzo CSV export: parsing, importing into a chosen account, source categories and pots; and the demo export."""
from datetime import date

import pytest

from slopfi import categorise, db, importer
from slopfi.parsers import detect_parser
from slopfi.parsers.monzo_csv import parse, sniff

from factories import EMPLOYER, MONZO_CSV_HEADER as HEADER, rule

ROWS = (
    "tx_1,27/05/2026,09:00:00,Bacs (Direct Credit),Acme Analytics Ltd,,Income,3912.40,GBP,3912.40,GBP,SALARY,,,SALARY,,,3912.40\n"
    "tx_2,27/05/2026,10:00:00,Faster payment,Alex Rivera,,Bills,-700.00,GBP,-700.00,GBP,JointBills,,,JointBills,,-700.00,\n"
    "tx_3,01/05/2026,09:27:20,Card payment,Deliveroo,🍝,Eating out,-18.45,GBP,-18.45,GBP,,1 Example Street,,DELIVEROO              LONDON        GBR,,-18.45,\n"
    "tx_4,02/05/2026,09:27:20,Pot transfer,Savings Pot,,Savings,-6.70,GBP,-6.70,GBP,,,,Round up,,-6.70,\n"
    "tx_5,03/05/2026,12:00:00,Card payment,Cafe Central,,Eating out,-20.31,GBP,-23.80,EUR,,,,CAFE CENTRAL MALAGA ESP,,-20.31,\n"
    "tx_6,04/05/2026,12:00:00,Card payment,Riverside Market Ltd,,General,-30.00,GBP,-30.00,GBP,,,,RIVERSIDE MARKET EXAMPLETOWN GBR,,-30.00,\n"
)

# What files the rows above: the salary and the transfer by rule; the pot transfer, the café and the market are
# left to Monzo's own categories (the market's 'General' maps to nothing).
RULES = [
    rule(EMPLOYER, "Income/Salary", priority=10),
    rule("RIVERA", "Transfers/Between own accounts", priority=20),
]


def _write(tmp_path):
    f = tmp_path / "Monzo_export.csv"
    f.write_text(HEADER + ROWS, encoding="utf-8")
    return f


def test_sniff_and_parse(tmp_path):
    f = _write(tmp_path)
    assert sniff(f) and detect_parser(f).PARSER_NAME == "monzo_csv"
    st = parse(f)
    assert st.requires_account and st.period_start == date(2026, 5, 1) and st.period_end == date(2026, 5, 27)
    by_id = {t.external_id: t for t in st.transactions}
    assert by_id["tx_1"].type_code == "BACS" and by_id["tx_1"].amount == 3912.40
    assert by_id["tx_2"].description == "Alex Rivera / JointBills"
    assert by_id["tx_3"].description == "Deliveroo / DELIVEROO LONDON GBR"
    assert by_id["tx_4"].description == "Savings Pot / Round up" and by_id["tx_4"].type_code == "POT"
    assert by_id["tx_5"].fx_currency == "EUR" and by_id["tx_5"].fx_amount == 23.8 and by_id["tx_5"].fx_rate == 1.1718
    assert st.reconcile() == []


def test_import_requires_account_and_uses_source_categories(conn, tmp_path, make_rules):
    make_rules(conn, RULES)
    f = _write(tmp_path)
    (r,) = importer.import_file(conn, f)
    assert r.status == "error" and "choose an account" in r.message

    acct = importer.get_or_create_account(conn, "Monzo Alex", "alex", "current", "Monzo")
    (r,) = importer.import_file(conn, f, account_id=acct)
    assert r.status == "imported" and r.inserted == 6
    rows = {row["description"]: row for row in conn.execute(
        "SELECT t.description, t.categorised_by, c.name AS cat FROM transactions t LEFT JOIN categories c ON c.id = t.category_id")}
    assert rows["Acme Analytics Ltd / SALARY"]["cat"] == "Salary" and rows["Acme Analytics Ltd / SALARY"]["categorised_by"] == "rule"
    assert rows["Alex Rivera / JointBills"]["cat"] == "Between own accounts"
    assert rows["Alex Rivera / JointBills"]["categorised_by"] == "rule"                 # the rule beats Monzo's 'Bills'
    assert rows["Savings Pot / Round up"]["cat"] == "Savings"
    assert rows["Cafe Central / CAFE CENTRAL MALAGA ESP"]["categorised_by"] == "source"   # Monzo said Eating out
    assert rows["Cafe Central / CAFE CENTRAL MALAGA ESP"]["cat"] == "Eating out"
    assert rows["Riverside Market Ltd / RIVERSIDE MARKET EXAMPLETOWN GBR"]["cat"] is None   # 'General' maps to nothing

    # re-import of an overlapping export (different file, same ids) adds nothing
    g = tmp_path / "Monzo_export2.csv"
    g.write_text(HEADER + ROWS.splitlines(keepends=True)[0], encoding="utf-8")
    (r2,) = importer.import_file(conn, g, account_id=acct)
    assert r2.inserted == 0 and r2.skipped == 1

    # a new rule beats the bank's own category
    categorise.create_rule(conn, "CAFE CENTRAL", db.category_id_by_path(conn, "Leisure/Holidays"))
    assert categorise.apply_rules(conn) == 1
    cat = conn.execute("SELECT c.name FROM transactions t JOIN categories c ON c.id = t.category_id WHERE t.description LIKE 'Cafe%'").fetchone()
    assert cat["name"] == "Holidays"


POT_ROWS = (
    "tx_p1,01/07/2026,01:52:32,Pot transfer, Pot,,Savings,500.00,GBP,500.00,GBP,,,,,,,500.00\n"
    "tx_p2,01/07/2026,04:06:40,pot-savings,,,Savings,6.15,GBP,6.15,GBP,,,,Interest for June 2026,,,6.15\n"
    "tx_p3,02/07/2026,09:00:00,Pot transfer, Pot,,Savings,-0.30,GBP,-0.30,GBP,,,,Round up,,-0.30,\n"
)


def test_pot_export(conn, tmp_path, make_rules):
    # Monzo labels all three 'Savings'; a rule picks the interest out
    make_rules(conn, [rule("INTEREST FOR", "Income/Interest received", "prefix", priority=10, type_code="INT")])
    f = tmp_path / "Monzo_pot.csv"
    f.write_text(HEADER + POT_ROWS, encoding="utf-8")
    st = parse(f)
    assert st.account_kind == "savings"
    assert [t.type_code for t in st.transactions] == ["POT", "INT", "POT"]
    assert st.transactions[0].description == "Pot transfer"
    assert st.transactions[1].description == "Interest for June 2026"
    acct = importer.get_or_create_account(conn, "Monzo Savings Pot", "alex", "savings")
    (r,) = importer.import_file(conn, f, account_id=acct)
    assert r.inserted == 3 and r.categorised == 3
    rows = conn.execute("""SELECT c.name, t.categorised_by FROM transactions t JOIN categories c ON c.id = t.category_id
                           ORDER BY t.date, t.seq""").fetchall()
    assert [tuple(row) for row in rows] == [("Savings", "source"), ("Interest received", "rule"), ("Savings", "source")]


# ------------------------------------------------------------ generated CSV
def test_generated_export_parses_and_imports(demo_statements, conn):
    (path,) = demo_statements.of("sam-monzo")
    acct = demo_statements.account("sam-monzo")
    assert detect_parser(path).PARSER_NAME == "monzo_csv"
    st = parse(path)
    assert st.requires_account and st.reconcile() == [] and st.warnings == []
    assert st.opening_balance is None and st.closing_balance is None       # an export carries no balances
    assert (st.period_start, st.period_end) == (demo_statements.household.start, demo_statements.household.end)
    assert [(t.date, t.amount) for t in st.transactions] == [(t.day, t.amount) for t in acct.txns]
    assert len({t.external_id for t in st.transactions}) == len(st.transactions)
    codes = {t.type_code for t in st.transactions}
    assert {"CARD", "DD", "FP", "BACS"} <= codes
    fx = [t for t in st.transactions if t.fx_currency]
    assert [(t.fx_currency, t.fx_amount) for t in fx] == [("EUR", 9.4), ("EUR", 37.85), ("EUR", 24.0)]
    assert all(t.amount == pytest.approx(-t.fx_amount / t.fx_rate, abs=0.01) for t in fx)
    salary = [t for t in st.transactions if t.type_code == "BACS"]
    assert len(salary) == 12 and all(t.amount == 2684.20 and t.suggested_category == "Income" for t in salary)

    account_id = importer.get_or_create_account(conn, "Monzo Sam", "sam", "current", "Monzo")
    (r,) = importer.import_file(conn, path, account_id=account_id)
    assert r.status == "imported" and r.inserted == len(st.transactions)
    assert r.categorised > len(st.transactions) // 2                       # Monzo's own categories, no rules
    (again,) = importer.import_file(conn, path, account_id=account_id)
    assert again.status == "duplicate"
