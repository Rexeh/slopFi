"""HSBC current account parser: the row state machine on synthetic rows, and the generated demo statements."""
from datetime import date

import pymupdf
import pytest

from slopfi.demo import writers
from slopfi.parsers import ParsedStatement, ParseError, detect_parser
from slopfi.parsers.hsbc_current import Columns, Row, Word, _State, _parse_period, parse, sniff

import factories


def test_parse_period_variants():
    assert _parse_period("26 June to 25 July 2026") == (date(2026, 6, 26), date(2026, 7, 25))
    assert _parse_period("26 December to 25 January 2026") == (date(2025, 12, 26), date(2026, 1, 25))
    assert _parse_period("26 December 2025 to 25 January 2026") == (date(2025, 12, 26), date(2026, 1, 25))


def _row(y, *words):
    return Row(y, [Word(x0, x1, t) for x0, x1, t in words])


def _stmt(**kw) -> ParsedStatement:
    base = dict(parser="hsbc_current", institution="HSBC UK", account_name=factories.JOINT_HOLDER,
                account_identifier=factories.JOINT_ID, account_kind="current", period_start=date(2026, 6, 26),
                period_end=date(2026, 7, 25), opening_balance=100.0, closing_balance=None, payments_in=None,
                payments_out=None)
    base.update(kw)
    return ParsedStatement(**base)


def test_state_machine_synthetic_rows():
    cols = Columns()
    st = _stmt(closing_balance=16.73, payments_in=50.0, payments_out=133.27)
    s = _State(st)
    rows = [
        _row(10, (53, 61, "25"), (63, 76, "Jun"), (78, 86, "26"), (140, 179, "BALANCE"), (180, 221, "BROUGHT"), (223, 264, "FORWARD"), (313, 315, "."), (529, 551, "100.00")),
        _row(20, (53, 61, "27"), (63, 74, "Jun"), (76, 84, "26"), (113, 124, "CR"), (140, 158, "Sam"), (160, 186, "Rivera")),
        _row(30, (140, 183, "JointBills"), (449, 471, "50.00"), (522, 550, "150.00")),
        _row(40, (53, 61, "29"), (63, 74, "Jun"), (76, 84, "26"), (113, 121, ")))"), (140, 160, "INT'L"), (162, 202, "0099001234")),
        _row(50, (140, 162, "CAFE"), (164, 197, "CENTRAL")),
        _row(60, (140, 156, "EUR"), (158, 177, "23.80"), (179, 187, "@"), (189, 211, "1.1720")),
        _row(70, (140, 155, "Visa"), (157, 172, "Rate"), (366, 390, "20.31")),
        _row(80, (113, 124, "DR"), (140, 182, "Non-Sterling")),
        _row(90, (140, 178, "Transaction"), (180, 192, "Fee"), (376, 390, "0.56"), (522, 550, "129.13")),
        _row(100, (53, 61, "30"), (63, 74, "Jun"), (76, 84, "26"), (113, 125, "DD"), (140, 158, "MTG"), (160, 196, "12349876"), (365, 389, "112.40"), (529, 551, "16.73")),
        _row(110, (140, 178, "BALANCE"), (180, 214, "CARRIED"), (216, 257, "FORWARD"), (529, 551, "16.73")),
    ]
    for r in rows:
        s.feed(r, cols, 1)
    s.finish()
    t = st.transactions
    assert [x.type_code for x in t] == ["CR", ")))", "DR", "DD"]
    assert t[0].amount == 50.0 and t[0].balance_after == 150.0 and t[0].description == "Sam Rivera / JointBills"
    assert t[1].amount == -20.31 and t[1].fx_currency == "EUR" and t[1].fx_amount == 23.8 and t[1].fx_rate == 1.172
    assert t[2].amount == -0.56 and t[2].balance_after == 129.13
    assert t[3].date == date(2026, 6, 30) and t[3].amount == -112.40
    assert st.reconcile() == []


def test_overdrawn_balance_and_reconcile_failure():
    cols = Columns()
    st = _stmt(opening_balance=10.0, closing_balance=-5.0, payments_in=0.0, payments_out=15.0)
    s = _State(st)
    s.feed(_row(10, (53, 61, "01"), (63, 74, "Jul"), (76, 84, "26"), (113, 125, "DD"), (140, 160, "WATER"), (367, 389, "15.00"), (533, 551, "5.00"), (555, 560, "D")), cols, 1)
    s.finish()
    assert st.transactions[0].balance_after == -5.0

    bad = _stmt(opening_balance=10.0, closing_balance=0.0, payments_in=0.0, payments_out=15.0)
    s2 = _State(bad)
    s2.feed(_row(10, (53, 61, "01"), (63, 74, "Jul"), (76, 84, "26"), (113, 125, "DD"), (140, 160, "WATER"), (367, 389, "15.00")), cols, 1)
    with pytest.raises(ParseError):
        s2.finish()


# ----------------------------------------------------------- generated PDFs
@pytest.fixture(scope="module")
def hsbc(demo_statements):
    """(path, parsed statement, the household's transactions for that month) for each of the twelve months."""
    acct = demo_statements.account("joint-hsbc")
    out = []
    for path in demo_statements.of("joint-hsbc"):
        st = parse(path)
        out.append((path, st, [t for t in acct.txns if st.period_start <= t.day <= st.period_end]))
    return out


def test_generated_statements_are_detected_and_parse_cleanly(hsbc):
    assert len(hsbc) == 12
    for path, st, _ in hsbc:
        assert sniff(path) and detect_parser(path).PARSER_NAME == "hsbc_current"
        assert st.reconcile() == [], path.name
        assert st.warnings == [], (path.name, st.warnings)
        assert st.account_identifier == factories.JOINT_ID and st.account_name == factories.JOINT_HOLDER
        assert st.institution == "HSBC UK" and st.account_kind == "current" and not st.requires_account
        assert st.period_start.day == 1 and st.period_end.month == st.period_start.month
        assert all(st.period_start <= t.date <= st.period_end for t in st.transactions)


def test_every_printed_transaction_is_read_back_exactly(hsbc):
    for path, st, expected in hsbc:
        got = [(t.date, t.type_code, t.description, t.amount) for t in st.transactions]
        assert got == [(t.day, t.code, " / ".join(t.lines), t.amount) for t in expected], path.name


def test_balances_match_the_household_and_chain_month_to_month(hsbc, demo_statements):
    acct = demo_statements.account("joint-hsbc")
    for path, st, expected in hsbc:
        assert st.closing_balance == pytest.approx(acct.balance_on(st.period_end) / 100), path.name
        assert st.payments_in == pytest.approx(sum(t.pence for t in expected if t.pence > 0) / 100)
        assert st.payments_out == pytest.approx(-sum(t.pence for t in expected if t.pence < 0) / 100)
        # the balance is printed once per day, on the day's last transaction
        printed = [t for t in st.transactions if t.balance_after is not None]
        assert len(printed) == len({t.date for t in st.transactions})
        assert printed[-1].balance_after == pytest.approx(st.closing_balance)
    assert hsbc[0][1].opening_balance == acct.opening / 100
    for (_, before, _), (_, after, _) in zip(hsbc, hsbc[1:]):
        assert after.opening_balance == pytest.approx(before.closing_balance)


def test_multi_page_statements_carry_the_balance_forward(hsbc):
    for path, st, _ in hsbc:
        with pymupdf.open(path) as doc:
            pages = [page.get_text() for page in doc]
        assert len(pages) >= 2, path.name
        assert "BALANCE CARRIED FORWARD" in pages[0] and "BALANCE BROUGHT FORWARD" in pages[1]
        # the month's last transaction sits on the last page and was read, after everything on page 1
        assert st.transactions[-1].lines[0] in pages[-1] and st.transactions[0].lines[0] in pages[0]


def test_foreign_currency_payments_and_their_fee_lines(hsbc):
    august = next(st for _, st, _ in hsbc if st.period_start == date(2026, 8, 1))
    fx = [t for t in august.transactions if t.fx_currency]
    assert [(t.fx_currency, t.fx_amount) for t in fx] == [("EUR", 23.8), ("EUR", 17.45), ("EUR", 31.2)]
    for t in fx:
        assert t.type_code == "VIS" and t.amount < 0 and 1.16 < t.fx_rate < 1.19
        assert t.amount == pytest.approx(-round(t.fx_amount / t.fx_rate, 2), abs=0.01)
        assert t.lines[-1] == f"EUR {t.fx_amount:.2f} @ {t.fx_rate:.4f}"
        fee = august.transactions[august.transactions.index(t) + 1]
        assert fee.description == "NON-STERLING / TRANSACTION FEE" and fee.type_code == "DR" and fee.amount < 0
    assert not any(t.fx_currency for _, st, _ in hsbc if st is not august for t in st.transactions)


def test_credits_are_positive(hsbc):
    for path, st, _ in hsbc:
        credits = [t for t in st.transactions if t.amount > 0]
        assert {t.type_code for t in credits} == {"CR"}, path.name
        assert any(t.description == factories.SALARY for t in credits)
        assert any(t.description == "S RIVERA / JOINT ACCOUNT" and t.amount == 1350.0 for t in credits)
    may = next(st for _, st, _ in hsbc if st.period_start == date(2026, 5, 1))
    assert any(t.description == "BRIGHTSPARK ENERGY / REFUND" and t.amount == 86.40 for t in may.transactions)


def test_an_overdrawn_statement_reads_negative_balances(tmp_path):
    acct = factories.demo_account(opening=40.0, txns=[
        (date(2026, 3, 2), -120.0, ["EXAMPLE HOME LOANS", "MTG 12349876"], "DD"),
        (date(2026, 3, 9), -15.5, ["EXAMPLE WATER"], "DD"),
        (date(2026, 3, 27), 200.0, [factories.EMPLOYER, "SALARY"], "CR"),
    ])
    path = writers.write_hsbc(acct, 2026, 3, tmp_path / "overdrawn.pdf")
    st = parse(path)
    assert st.reconcile() == [] and st.warnings == []
    assert [t.balance_after for t in st.transactions] == [-80.0, -95.5, 104.5]
    assert st.opening_balance == 40.0 and st.closing_balance == 104.5


def test_an_overdrawn_long_month_over_three_pages(tmp_path):
    days = [date(2026, 4, 1 + i % 30) for i in range(120)]
    acct = factories.demo_account(opening=-10.0, txns=[(d, -1.25, ["CORNER SHOP EXAMPLETOWN"], ")))") for d in sorted(days)])
    path = writers.write_hsbc(acct, 2026, 4, tmp_path / "long.pdf")
    with pymupdf.open(path) as doc:
        assert len(doc) >= 3
    st = parse(path)
    assert st.reconcile() == [] and len(st.transactions) == 120
    assert st.opening_balance == -10.0 and st.closing_balance == -160.0
    assert st.transactions[-1].balance_after == -160.0
