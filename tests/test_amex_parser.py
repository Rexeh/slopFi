"""American Express parser: helpers, and the generated demo statements (credits, foreign spend, page breaks)."""
from datetime import date

import pymupdf
import pytest

from slopfi.demo import writers
from slopfi.parsers import detect_parser
from slopfi.parsers.amex_card import _money, _parse_period, _txn_date, parse

import factories


def test_helpers():
    assert _parse_period("Statement Period From 6 June to 5 July 2026") == (date(2026, 6, 6), date(2026, 7, 5))
    assert _parse_period("From 6 December to 5 January 2027") == (date(2026, 12, 6), date(2027, 1, 5))
    s, e = date(2026, 12, 6), date(2027, 1, 5)
    assert _txn_date("Dec", "20", s, e) == date(2026, 12, 20)
    assert _txn_date("Jan", "3", s, e) == date(2027, 1, 3)
    assert _money(".65") == 0.65 and _money("1,234.56") == 1234.56 and _money("CR") is None and _money("12,34") == 12.34


@pytest.fixture(scope="module")
def amex(demo_statements):
    acct = demo_statements.account("alex-amex")
    out = []
    for path in demo_statements.of("alex-amex"):
        st = parse(path)
        out.append((path, st, [t for t in acct.txns if st.period_start <= t.day <= st.period_end]))
    return out


def test_generated_statements_are_detected_and_reconcile(amex):
    assert len(amex) == 12
    for path, st, _ in amex:
        module = detect_parser(path)
        assert module is not None and module.PARSER_NAME == "amex_card"
        assert st.reconcile() == [] and st.warnings == [], path.name
        assert st.account_kind == "credit_card" and st.account_identifier == factories.CARD_ID
        assert st.account_name == "ALEX RIVERA" and st.institution == "American Express"
        assert st.closing_balance <= 0                     # a card balance is money owed


def test_every_transaction_is_read_back(amex):
    for path, st, expected in amex:
        got = [(t.date, t.amount, t.lines[:len(e.lines)]) for t, e in zip(st.transactions, expected)]
        assert len(st.transactions) == len(expected), path.name
        assert got == [(e.day, e.amount, e.lines) for e in expected], path.name


def test_balances_are_owed_and_chain_month_to_month(amex, demo_statements):
    acct = demo_statements.account("alex-amex")
    assert amex[0][1].opening_balance == acct.opening / 100
    for path, st, _ in amex:
        assert st.closing_balance == pytest.approx(acct.balance_on(st.period_end) / 100), path.name
    for (_, before, _), (_, after, _) in zip(amex, amex[1:]):
        assert after.opening_balance == pytest.approx(before.closing_balance)


def test_payments_and_refunds_are_credits(amex):
    for path, st, _ in amex:
        credits = [t for t in st.transactions if t.amount > 0]
        assert credits and all(t.type_code == "CR" for t in credits), path.name
        payment = next(t for t in credits if t.description == "PAYMENT RECEIVED - THANK YOU")
        assert payment.date.day == 20 and payment.amount == pytest.approx(-st.opening_balance)
    refunds = [t for _, st, _ in amex for t in st.transactions if t.amount > 0 and "PAYMENT" not in t.description]
    assert {t.lines[0] for t in refunds} == {"AMAZON.CO.UK", "NEXT RETAIL"}


def test_foreign_spend_carries_amount_rate_and_fee(amex):
    fx = [t for _, st, _ in amex for t in st.transactions if t.fx_currency]
    assert len(fx) == 1
    (hotel,) = fx
    assert hotel.lines[:2] == ["HOTEL MIRAMAR", "MALAGA"] and hotel.date == date(2026, 8, 17)
    assert hotel.fx_currency == "EUR" and hotel.fx_amount == 980.0 and 1.15 < hotel.fx_rate < 1.18
    assert hotel.lines[2].startswith(f"Exchange Rate {hotel.fx_rate:.4f} + Nonsterling Transaction Fee ")
    fee = float(hotel.lines[2].rsplit(" ", 1)[1])
    assert hotel.amount == pytest.approx(-(round(980.0 / hotel.fx_rate, 2) + fee), abs=0.02)


def test_a_statement_that_runs_onto_a_second_page(tmp_path):
    rows = [(date(2026, 3, 1 + i % 28), -(5.0 + i), ["ONLINE BOOKSHOP", "EXAMPLETOWN"], "CARD") for i in range(40)]
    rows.append((date(2026, 3, 20), 120.0, ["PAYMENT RECEIVED - THANK YOU"], "CR"))
    rows.sort(key=lambda r: r[0])
    acct = factories.demo_account("card", "amex_pdf", opening=-120.0, txns=rows)
    path = writers.write_amex(acct, 2026, 3, tmp_path / "long.pdf")
    with pymupdf.open(path) as doc:
        assert len(doc) >= 2
    st = parse(path)
    assert st.reconcile() == [] and len(st.transactions) == 41
    spend = sum(5.0 + i for i in range(40))
    assert st.closing_balance == pytest.approx(-spend) and st.payments_out == pytest.approx(spend)
    assert [t.amount for t in st.transactions if t.amount > 0] == [120.0]
