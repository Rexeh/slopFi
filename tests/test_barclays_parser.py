"""Barclays current account parser: helpers, and generated statements in Barclays' layout (wrapped descriptions,
reference and cash-machine lines, page breaks, a year-end period, an overdrawn month, the importer round trip).

The reference layout came from a real statement; nothing here is copied from it. Every name, sort code and
account number is the fictional Rivera household's."""
from datetime import date

import pymupdf
import pytest

from slopfi import importer
from slopfi.demo import household as demo_household, writers
from slopfi.parsers import ParseError, detect_parser
from slopfi.parsers.barclays_current import _classify, _parse_period, _txn_date, parse, sniff

import factories

ID, HOLDER = factories.BARCLAYS_ID, factories.BARCLAYS_HOLDER


# ------------------------------------------------------------------ helpers
def test_parse_period_variants():
    assert _parse_period("Barclays Bank Account 01 Mar - 31 Mar 2026 Mr Alex Rivera") == (date(2026, 3, 1), date(2026, 3, 31))
    assert _parse_period("29 Feb - 31 Mar 2020") == (date(2020, 2, 29), date(2020, 3, 31))
    assert _parse_period("30 Dec - 31 Jan 2027") == (date(2026, 12, 30), date(2027, 1, 31))
    with pytest.raises(ParseError):
        _parse_period("no period here")


def test_txn_dates_take_their_year_from_the_period():
    s, e = date(2026, 12, 30), date(2027, 1, 31)
    assert _txn_date("31 Dec", s, e) == date(2026, 12, 31)
    assert _txn_date("02 Jan", s, e) == date(2027, 1, 2)
    assert _txn_date("15 Mar", date(2026, 3, 1), date(2026, 3, 31)) == date(2026, 3, 15)


def test_classify_splits_the_type_prefix_the_payee_and_the_trailing_lines():
    # The rows of one transaction are re-joined first (Barclays wraps at the column edge, mid-phrase), then split
    # back into the payee, the purchase date and the reference/time lines.
    assert _classify("Card Payment to Shell Wembley Park On 28 Feb") == ("CARD", ["Shell Wembley Park", "On 28 Feb"])
    assert _classify("Card Payment to Costcutter On 06 Mar") == ("CARD", ["Costcutter", "On 06 Mar"])
    assert _classify("Direct Debit to Brightspark Energy Ref: 000000000036781350") == \
        ("DD", ["Brightspark Energy", "Ref: 000000000036781350"])
    assert _classify("Direct Debit to Example Water Ref: 851014405259 This Is A New Direct Debit Payment") == \
        ("DD", ["Example Water", "Ref: 851014405259", "This Is A New Direct Debit Payment"])
    assert _classify("Received From Acme Analytics Ltd Ref: Salary") == ("CR", ["Acme Analytics Ltd", "Ref: Salary"])
    assert _classify("Bill Payment to S Rivera Ref: Joint Account") == ("BP", ["S Rivera", "Ref: Joint Account"])
    assert _classify("Cash Machine Withdrawal at Barclays Exampletown 4 Timed at 21.41 On 7 Mar") == \
        ("ATM", ["Barclays Exampletown 4", "Timed at 21.41 On 7 Mar"])
    assert _classify("Standing Order to Example Savings Ref: Monthly") == ("SO", ["Example Savings", "Ref: Monthly"])
    assert _classify("Mystery Payment to Somewhere") == (None, ["Mystery Payment to Somewhere"])


def test_classify_knows_the_prefixes_newer_statements_print():
    # A 2026 statement says "Card Purchase", "Transfer From", "Payment to" and "Refund From".
    assert _classify("Card Purchase Example Deli - Main On 22 Jun") == ("CARD", ["Example Deli - Main", "On 22 Jun"])
    assert _classify("Transfer From Sort Code 99-10-20 Account 12341020 Ref: Optional") == \
        ("TFR", ["Sort Code 99-10-20 Account 12341020", "Ref: Optional"])
    assert _classify("Transfer To Sort Code 99-10-20 Account 12341020 Ref: Savings") == \
        ("TFR", ["Sort Code 99-10-20 Account 12341020", "Ref: Savings"])
    assert _classify("Payment to S Rivera Ref: Food Money") == ("FP", ["S Rivera", "Ref: Food Money"])
    assert _classify("Refund From Paypal *Examplebra On 08 Jul") == ("CR", ["Paypal *Examplebra", "On 08 Jul"])


def test_classify_splits_a_foreign_card_purchase_like_the_hsbc_parser():
    text = ("Card Purchase Cafe Praia Portugal EUR 3.50 On 26 Jun at VISA Exchange Rate 1.16 The Final GBP Amount "
            "Includes A Non-Sterling Transaction Fee of £ 0.09")
    assert _classify(text) == ("CARD", ["Cafe Praia Portugal", "On 26 Jun", "EUR 3.50 @ 1.16",
                                        "Non-Sterling Transaction Fee £0.09"])
    assert _classify("Card Purchase Hotel Mar Portugal EUR 120.00 On 27 Jun at VISA Exchange Rate 1.1623") == \
        ("CARD", ["Hotel Mar Portugal", "On 27 Jun", "EUR 120.00 @ 1.1623"])


# ------------------------------------------------------- a typical month
# (day, pounds, lines as the parser reports them, type code). Several transactions a day, a day with one, a payee
# long enough to wrap, a reference line, a cash machine line and a credit with a reference.
MARCH = [
    (date(2026, 3, 2), -20.56, ["Dvla-Kb03mze", "Ref: 000000000036781350"], "DD"),
    (date(2026, 3, 2), -128.34, ["Fibreline Broadband Pymts", "Ref: 722433004001"], "DD"),
    (date(2026, 3, 2), -2.52, ["Shell Exampletown Park", "On 28 Feb"], "CARD"),
    (date(2026, 3, 2), -11.40, ["Cafe Central Ltd", "On 28 Feb"], "CARD"),
    (date(2026, 3, 4), -5.40, ["Esso Example Svs Stn", "On 03 Mar"], "CARD"),
    (date(2026, 3, 4), 885.11, ["Acme Analytics Ltd", "Ref: B33v2qtzise9"], "CR"),
    (date(2026, 3, 9), -62.50, ["Brightspark Energy", "Ref: 851014405259", "This Is A New Direct Debit Payment"], "DD"),
    (date(2026, 3, 9), -10.00, ["Barclays Exampletown 4", "Timed at 21.41 On 7 Mar"], "ATM"),
    (date(2026, 3, 9), -41.71, ["Mercado Das Carnes Exampletown", "On 08 Mar"], "CARD"),
    (date(2026, 3, 9), -104.00, ["S Rivera", "Ref: Joint Account"], "BP"),
    (date(2026, 3, 12), -89.03, ["Tesco Store 2122", "On 11 Mar"], "CARD"),
    (date(2026, 3, 23), -1260.00, ["Example Estates", "Ref: Rent"], "BP"),
    (date(2026, 3, 23), 300.00, ["Sam Rivera", "Ref: Sent From Monzo"], "CR"),
    (date(2026, 3, 30), -6.85, ["Maxxi Save International Stores", "On 28 Mar"], "CARD"),
]


@pytest.fixture(scope="module")
def march(tmp_path_factory):
    acct = factories.demo_account("barclays", "barclays_pdf", opening=602.56, txns=MARCH)
    path = writers.write_barclays(acct, 2026, 3, tmp_path_factory.mktemp("barclays") / "barclays-2026-03.pdf")
    return path, parse(path), acct


def test_generated_statement_is_detected_and_parses_cleanly(march, demo_statements):
    path, st, _ = march
    assert sniff(path) and detect_parser(path).PARSER_NAME == "barclays_current"
    assert st.parser == "barclays_current" and st.institution == "Barclays"
    assert st.account_identifier == ID and st.account_name == HOLDER and not st.requires_account
    assert st.account_kind == "current"
    assert (st.period_start, st.period_end) == (date(2026, 3, 1), date(2026, 3, 31))
    assert st.reconcile() == [] and st.warnings == []
    # and the other PDF parsers do not claim it, nor does this one claim theirs
    assert not any(m.sniff(path) for m in [detect_parser(p) for p in demo_statements.files if p.suffix == ".pdf"]
                   if m.PARSER_NAME != "barclays_current")
    assert all(not sniff(p) for p in demo_statements.files if p.suffix == ".pdf")


def test_every_printed_transaction_is_read_back_exactly(march):
    _, st, _ = march
    got = [(t.date, t.type_code, t.lines, t.amount) for t in st.transactions]
    assert got == [(d, code, lines, pounds) for d, pounds, lines, code in MARCH]
    assert st.transactions[2].description == "Shell Exampletown Park / On 28 Feb"


def test_summary_and_balances(march):
    _, st, acct = march
    assert st.opening_balance == 602.56
    assert st.payments_in == pytest.approx(885.11 + 300.00)
    assert st.payments_out == pytest.approx(sum(-p for _, p, _, _ in MARCH if p < 0))
    assert st.closing_balance == pytest.approx(acct.balance_on(date(2026, 3, 31)) / 100)
    # the balance is printed once per day, on the day's last transaction
    printed = [t for t in st.transactions if t.balance_after is not None]
    assert len(printed) == len({t.date for t in st.transactions})
    assert all(t.balance_after is not None for i, t in enumerate(st.transactions)
               if i == len(st.transactions) - 1 or st.transactions[i + 1].date != t.date)
    assert printed[-1].balance_after == pytest.approx(st.closing_balance)
    assert st.transactions[3].balance_after == pytest.approx(602.56 - 20.56 - 128.34 - 2.52 - 11.40)


def test_credits_are_positive_and_debits_negative(march):
    _, st, _ = march
    assert {t.type_code for t in st.transactions if t.amount > 0} == {"CR"}
    assert {t.type_code for t in st.transactions if t.amount < 0} == {"DD", "CARD", "ATM", "BP"}


def test_the_information_pages_after_the_table_are_ignored(march):
    path, st, _ = march
    with pymupdf.open(path) as doc:
        assert len(doc) >= 2 and "compensation" in doc[-1].get_text().lower()
    assert len(st.transactions) == len(MARCH)


# ---------------------------------------------------------- page breaks
def test_a_long_month_runs_over_several_pages_and_stays_in_order(tmp_path):
    txns = [(date(2026, 4, 1 + i % 30), round(-1.25 - i * 0.01, 2), [f"Corner Shop {i:03d} Exampletown", "On 01 Apr"], "CARD")
            for i in range(90)]
    txns.sort(key=lambda t: t[0])
    acct = factories.demo_account("long", "barclays_pdf", opening=500.0, txns=txns)
    path = writers.write_barclays(acct, 2026, 4, tmp_path / "long.pdf")
    with pymupdf.open(path) as doc:
        pages = [p.get_text() for p in doc]
    assert len(pages) >= 4 and "Continued" in pages[0] and "Continued" in pages[1]
    assert "End balance" in pages[-2] and "Continued" not in pages[-2]   # the table ends before the info page
    st = parse(path)
    assert st.reconcile() == [] and st.warnings == []
    assert [(t.date, t.lines, t.amount) for t in st.transactions] == [(d, lines, p) for d, p, lines, _ in txns]
    assert st.transactions[-1].balance_after == pytest.approx(st.closing_balance)
    # a day's transactions can straddle a page: the date is only printed once, on the first page
    assert all(t.date.month == 4 for t in st.transactions)


# ------------------------------------------------------- awkward periods
def test_a_statement_period_that_straddles_the_year_end(tmp_path):
    txns = [
        (date(2026, 12, 30), -30.00, ["Greggs", "On 29 Dec"], "CARD"),
        (date(2026, 12, 31), 1500.00, ["Acme Analytics Ltd", "Ref: Salary"], "CR"),
        (date(2027, 1, 2), -12.00, ["Costcutter", "On 01 Jan"], "CARD"),
        (date(2027, 1, 29), -40.00, ["Example Water", "Ref: 123456"], "DD"),
    ]
    acct = factories.demo_account("ye", "barclays_pdf", opening=100.0, txns=txns)
    path = writers.write_barclays(acct, 2027, 1, tmp_path / "jan.pdf", period=(date(2026, 12, 30), date(2027, 1, 31)))
    st = parse(path)
    assert (st.period_start, st.period_end) == (date(2026, 12, 30), date(2027, 1, 31))
    assert [t.date for t in st.transactions] == [d for d, _, _, _ in txns]
    assert st.reconcile() == []


def test_an_overdrawn_statement_reads_negative_balances(tmp_path):
    # Assumption: Barclays prints an overdrawn balance with a leading minus. Not confirmed by the reference
    # statement, which never goes overdrawn.
    txns = [
        (date(2026, 5, 2), -120.00, ["Example Home Loans", "Ref: Mtg 12349876"], "DD"),
        (date(2026, 5, 9), -15.50, ["Example Water", "Ref: 1"], "DD"),
        (date(2026, 5, 27), 200.00, ["Acme Analytics Ltd", "Ref: Salary"], "CR"),
    ]
    acct = factories.demo_account("od", "barclays_pdf", opening=40.0, txns=txns)
    path = writers.write_barclays(acct, 2026, 5, tmp_path / "overdrawn.pdf")
    st = parse(path)
    assert st.reconcile() == [] and st.warnings == []
    assert [t.balance_after for t in st.transactions] == [-80.0, -95.5, 104.5]
    assert st.opening_balance == 40.0 and st.closing_balance == 104.5


def test_an_unknown_payment_type_is_kept_with_a_warning(tmp_path):
    txns = [(date(2026, 6, 3), -9.99, ["Somewhere"], "Mystery Payment to")]
    acct = factories.demo_account("odd", "barclays_pdf", opening=50.0, txns=txns)
    st = parse(writers.write_barclays(acct, 2026, 6, tmp_path / "odd.pdf"))
    assert st.reconcile() == []
    assert len(st.transactions) == 1 and st.transactions[0].type_code is None
    assert st.transactions[0].lines == ["Mystery Payment to Somewhere"] and st.transactions[0].amount == -9.99
    assert any("unknown payment type" in w for w in st.warnings)


def test_newer_prefixes_and_a_foreign_purchase_parse_without_warnings(tmp_path):
    # Txn.code is printed verbatim when the writer has no prefix for it, which is how the newer wording is produced.
    fx = demo_household.Txn(date(2026, 6, 27), -311, ["Cafe Praia Portugal", "On 26 Jun", "EUR 3.50 @ 1.16",
                                                        "Non-Sterling Transaction Fee £0.09"], "Card Purchase",
                            fx=("EUR", 350, "1.16"), fee_pence=9)
    acct = factories.demo_account("new", "barclays_pdf", opening=100.0, txns=[
        (date(2026, 6, 22), -4.50, ["Example Deli - Main", "On 22 Jun"], "Card Purchase"),
        (date(2026, 6, 24), 250.00, ["Sort Code 99-10-20 Account 12341020", "Ref: Optional"], "Transfer From"),
        (date(2026, 6, 25), -30.00, ["S Rivera", "Ref: Food Money"], "Payment to"),
        (date(2026, 7, 8), 12.99, ["Paypal *Examplebra", "On 08 Jul"], "Refund From"),
    ])
    acct.txns.insert(2, fx)
    st = parse(writers.write_barclays(acct, 2026, 6, tmp_path / "new.pdf", period=(date(2026, 6, 18), date(2026, 7, 17))))
    assert st.reconcile() == [] and st.warnings == []
    assert [t.type_code for t in st.transactions] == ["CARD", "TFR", "CARD", "FP", "CR"]
    assert [t.lines for t in st.transactions] == [t.lines for t in acct.txns]
    foreign = st.transactions[2]
    assert (foreign.fx_currency, foreign.fx_amount, foreign.fx_rate, foreign.amount) == ("EUR", 3.5, 1.16, -3.11)
    assert all(t.fx_currency is None for t in st.transactions if t is not foreign)


def test_a_statement_that_does_not_add_up_is_rejected(tmp_path):
    acct = factories.demo_account("bad", "barclays_pdf", opening=50.0, txns=[(date(2026, 6, 3), -9.99, ["Greggs"], "CARD")])
    path = writers.write_barclays(acct, 2026, 6, tmp_path / "bad.pdf")
    with pymupdf.open(path) as doc:
        page = doc[0]
        hit = next(r for r in page.search_for("9.99") if 250 < r.x0 < 310)   # the amount in the Money out column
        page.add_redact_annot(hit, text="8.99", fontsize=8)
        page.apply_redactions()
        doc.save(tmp_path / "tampered.pdf")
    with pytest.raises(ParseError, match="does not reconcile"):
        parse(tmp_path / "tampered.pdf")


# -------------------------------------------------------------- importer
def test_import_creates_the_account_and_skips_the_file_second_time(march, conn):
    path, _, _ = march
    first = importer.import_file(conn, path)
    assert [r.status for r in first] == ["imported"] and first[0].inserted == len(MARCH) and not first[0].warnings
    acct = conn.execute("SELECT * FROM accounts").fetchone()
    assert (acct["institution"], acct["identifier"], acct["kind"]) == ("Barclays", ID, "current")
    assert acct["name"] == "Barclays current …5060"
    assert [r.status for r in importer.import_file(conn, path)] == ["duplicate"]
    rows = conn.execute("SELECT type_code, description, amount FROM transactions ORDER BY seq").fetchall()
    assert rows[0]["type_code"] == "DD" and rows[0]["description"] == "Dvla-Kb03mze / Ref: 000000000036781350"
    assert rows[5]["amount"] == 885.11
