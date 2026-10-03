"""Monzo PDF parser: description classification, row assembly, and the generated statement (current account
plus two pots across many pages), imported end to end."""
import pymupdf
import pytest

from slopfi import importer
from slopfi.parsers import detect_parser, parse_file_all
from slopfi.parsers.monzo_pdf import _classify, _parse_rows

import factories


def test_classify():
    assert _classify("ACME ANALYTICS LTD (Direct Credit) Reference: SALARY", False) == ("BACS", ["ACME ANALYTICS LTD", "SALARY"], {})
    assert _classify("Sam Rivera (Faster Payments) Reference: JointBills", False)[:2] == ("FP", ["Sam Rivera", "JointBills"])
    assert _classify("Transfer to Pot", False)[0] == "POT"
    assert _classify("Interest for May 2026", True)[0] == "INT"
    assert _classify("Deposit (round up)", True)[0] == "POT"
    code, lines, fx = _classify("EXAMPLE GAMES INC NEW YORK USA Amount: USD -27.40. Exchange rate: 1.271234.", False)
    assert code == "CARD" and lines == ["EXAMPLE GAMES INC NEW YORK USA"]
    assert fx == {"fx_currency": "USD", "fx_amount": 27.4, "fx_rate": 1.271234}
    assert _classify("TRAINLINE.COM LONDON GBR This relates to a previous transaction", False)[1] == ["TRAINLINE.COM LONDON GBR"]


def _rows():
    # (y, [(x0, x1, text)...]) as group_rows produces; newest first like the PDF
    return [
        (10, [(71, 91, "Date"), (153, 204, "Description"), (405, 441, "Amount"), (489, 525, "Balance")]),
        (40, [(153, 186, "ACME"), (189, 240, "ANALYTICS"), (243, 258, "LTD"), (260, 290, "(Direct"), (292, 321, "Credit)")]),
        (47, [(71, 124, "28/09/2026"), (397, 441, "1,000.00"), (481, 525, "1,500.00")]),
        (54, [(153, 200, "Reference:"), (203, 236, "SALARY")]),
        (84, [(71, 124, "27/09/2026"), (153, 190, "Transfer"), (192, 201, "to"), (204, 219, "Pot"), (418, 441, "-4.00"), (491, 525, "500.00")]),
        (114, [(153, 186, "COSTA"), (189, 225, "COFFEE")]),
        (121, [(71, 124, "26/09/2026"), (418, 441, "-4.50"), (494, 525, "504.00")]),
        (128, [(153, 210, "EXAMPLETOWN"), (213, 233, "GBR")]),
        (160, [(71, 95, "Monzo"), (97, 115, "Bank"), (117, 143, "Limited")]),
    ]


def test_parse_rows_attaches_wrapped_descriptions_and_orders_chronologically():
    txns = _parse_rows([(_rows(), 0)], pot=False, warnings=[])
    assert [t.date.isoformat() for t in txns] == ["2026-09-26", "2026-09-27", "2026-09-28"]
    assert txns[0].description == "COSTA COFFEE EXAMPLETOWN GBR" and txns[0].amount == -4.5 and txns[0].balance_after == 504.0
    assert txns[1].type_code == "POT"
    assert txns[2].type_code == "BACS" and txns[2].description == "ACME ANALYTICS LTD / SALARY" and txns[2].amount == 1000.0


# ------------------------------------------------------------ generated PDF
@pytest.fixture(scope="module")
def monzo(demo_statements):
    (path,) = demo_statements.of("alex-monzo")
    return path, parse_file_all(path), demo_statements.account("alex-monzo")


def test_generated_statement_has_the_account_and_both_pots(monzo):
    path, stmts, acct = monzo
    assert detect_parser(path).PARSER_NAME == "monzo_pdf"
    assert [s.account_identifier for s in stmts] == [factories.MONZO_ID, f"{factories.MONZO_ID} pot:Savings",
                                                     f"{factories.MONZO_ID} pot:Rainy day"]
    assert [s.account_kind for s in stmts] == ["current", "savings", "savings"]
    assert [s.sub_account for s in stmts] == [None, "Savings", "Rainy day"]
    assert [s.account_name for s in stmts] == ["Monzo personal account", "Monzo pot: Savings", "Monzo pot: Rainy day"]
    for s in stmts:
        assert s.reconcile() == [] and s.warnings == [], s.account_name
        assert (s.period_start, s.period_end) == (acct.txns[0].day.replace(day=1), stmts[0].period_end)


def test_every_row_is_read_with_its_balance(monzo):
    _, (main, *pots), acct = monzo
    for st, opening, txns in [(main, acct.opening, acct.txns)] + [(s, p.opening, p.txns) for s, p in zip(pots, acct.pots)]:
        assert [(t.date, t.amount) for t in st.transactions] == [(t.day, t.amount) for t in txns], st.account_name
        running = opening
        for parsed, generated in zip(st.transactions, txns):
            running += generated.pence
            assert parsed.balance_after == pytest.approx(running / 100)
        assert st.opening_balance == opening / 100 and st.closing_balance == pytest.approx(running / 100)


def test_type_codes_for_transfers_pots_and_interest(monzo):
    _, (main, savings, rainy), _ = monzo
    codes = {t.type_code for t in main.transactions}
    assert codes == {"FP", "POT", "CARD"}
    incoming = [t for t in main.transactions if t.type_code == "FP"]
    assert incoming and all(t.description == "A & S RIVERA / MONZO SPENDING" and t.amount == 700.0 for t in incoming)
    to_pots = [t for t in main.transactions if t.type_code == "POT"]
    assert {t.amount for t in to_pots} == {-300.0, -50.0, 250.0}      # into both pots; back out of Rainy day
    for pot in (savings, rainy):
        assert {t.type_code for t in pot.transactions} == {"POT", "INT"}
        interest = [t for t in pot.transactions if t.type_code == "INT"]
        assert len(interest) == 12 and all(t.amount > 0 and t.description.startswith("Interest for ") for t in interest)
    assert [t.amount for t in rainy.transactions if t.amount < 0] == [-250.0]


def test_foreign_currency_card_payments(monzo):
    _, (main, *_), _ = monzo
    fx = [t for t in main.transactions if t.fx_currency]
    assert [(t.fx_currency, t.fx_amount) for t in fx] == [("EUR", 64.3), ("EUR", 48.5), ("EUR", 41.75), ("EUR", 22.0)]
    for t in fx:
        assert t.type_code == "CARD" and t.description.endswith(" ESP") and "Exchange rate" not in t.description
        assert t.amount == pytest.approx(-round(t.fx_amount / t.fx_rate, 2), abs=0.01)


def test_sections_span_many_pages(monzo):
    path, (main, *pots), _ = monzo
    with pymupdf.open(path) as doc:
        text = [page.get_text() for page in doc]
    titles = [i for i, t in enumerate(text) if "Personal Account statement" in t or "Pot statement" in t]
    assert len(text) > 10 and len(titles) == 3
    assert titles[1] - titles[0] > 2 and titles[2] - titles[1] >= 2   # every section runs over more than one page
    assert len(main.transactions) > 200 and all(len(p.transactions) > 20 for p in pots)


def test_import_creates_the_account_and_its_pots_once(monzo, conn):
    path, stmts, _ = monzo
    results = importer.import_file(conn, path, account=importer.AccountSpec(name="Monzo Alex", owner="alex", kind="current"))
    assert [r.status for r in results] == ["imported"] * len(stmts)
    accts = {r["name"]: r for r in conn.execute("SELECT * FROM accounts")}
    assert set(accts) == {"Monzo Alex", "Monzo pot: Savings", "Monzo pot: Rainy day"}
    assert accts["Monzo Alex"]["identifier"] == factories.MONZO_ID
    for s in stmts[1:]:
        assert accts[s.account_name]["kind"] == "savings" and accts[s.account_name]["owner"] == "alex"
    # second import of the same file: duplicate, nothing added
    again = importer.import_file(conn, path, account=importer.AccountSpec(name="Monzo Alex", owner="alex"))
    assert [r.status for r in again] == ["duplicate"]
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert n == sum(len(s.transactions) for s in stmts)
