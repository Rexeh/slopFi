"""Shared fakes for the test suite: rules, statements, transactions and generated statement files.

Everything here is made up. Names, merchants and identifiers come from the demo's fictional Rivera household
(slopfi.demo): sort codes start 99-, account numbers start 1234, the Amex membership ends 00000, the employer is
Acme Analytics Ltd and the address is 1 Example Street, Exampletown, EX1 1AA. A fresh database has no rules, so
a test that needs transactions filed declares the rules it relies on, using `rule()` and `add_rules()`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from slopfi import rules_io
from slopfi.demo import household as demo_household
from slopfi.demo import writers as demo_writers
from slopfi.parsers import ParsedStatement, ParsedTransaction

# --------------------------------------------------------------- identifiers
JOINT_SORT_CODE, JOINT_NUMBER = "99-10-20", "12341020"
JOINT_ID = f"{JOINT_SORT_CODE} {JOINT_NUMBER}"          # how the HSBC parser reports an account
JOINT_HOLDER = "MR A RIVERA & MS S RIVERA"
MONZO_ID = "99-30-40 12343040"
BARCLAYS_SORT_CODE, BARCLAYS_NUMBER = "99-50-60", "12345060"
BARCLAYS_ID = f"{BARCLAYS_SORT_CODE} {BARCLAYS_NUMBER}"
BARCLAYS_HOLDER = "Mr Alex Rivera"                      # Barclays prints the holder in title case
CARD_ID = "…00000"                                      # an Amex membership number as the parser reports it
EMPLOYER = "ACME ANALYTICS LTD"
SALARY = f"{EMPLOYER} / SALARY"
ENERGY = "BRIGHTSPARK ENERGY"
DEMO_REFERENCE = date(2026, 10, 3)                      # the demo year is Oct 2025 – Sep 2026
DEMO_SEED = 42


# --------------------------------------------------------------------- rules
def rule(pattern: str, category: str, match_type: str = "contains", priority: int = 50, **extra) -> dict:
    """One rule in the rules-file format (see slopfi.rules_io): extra keys are type_code, amount_min, amount_max,
    enabled."""
    return {"pattern": pattern, "match_type": match_type, "category": category, "priority": priority, **extra}


def add_rules(conn: sqlite3.Connection, rules: list[dict]) -> list[int]:
    """Install the rules, failing loudly if any is rejected. Returns the new rule ids in the order given."""
    before = conn.execute("SELECT COALESCE(MAX(id), 0) FROM rules").fetchone()[0]
    summary = rules_io.import_rules(conn, list(rules))
    assert not summary.errors, summary.errors
    assert summary.added == len(rules), f"{len(rules) - summary.added} duplicate rules"
    return [r[0] for r in conn.execute("SELECT id FROM rules WHERE id > ? ORDER BY id", (before,))]


# The everyday merchants the trend, spending, projection and page fixtures use, and a rule that files each.
EVERYDAY_RULES = [
    rule(EMPLOYER, "Income/Salary", priority=10),
    rule("RIVERA", "Transfers/Between own accounts", priority=20),
    rule(r"\b(TESCO|SAINSBURYS)\b", "Groceries/Supermarket", "regex"),
    rule("DELIVEROO", "Eating out/Takeaway & delivery"),
    rule(ENERGY, "Utilities/Energy", type_code="DD"),
    rule("NETFLIX", "Utilities/TV & streaming"),
    rule("EXAMPLE HOME LOANS", "Housing/Mortgage", type_code="DD"),
    rule("MOTORCARE", "Transport/Car maintenance"),
    rule("AMAZON", "Shopping/Online"),
    rule(r"^(ESSO|SHELL) ", "Transport/Fuel", "regex"),
    rule("NON-STERLING", "Bank charges/FX fees", priority=5),
    rule("INTEREST FOR", "Income/Interest received", "prefix", priority=10, type_code="INT"),
]


# -------------------------------------------------------------- transactions
def txn(day: str | date, description: str | list[str], amount: float, code: str | None = ")))",
        **kw) -> ParsedTransaction:
    """A parsed transaction. `description` is one printed line or a list of them."""
    lines = [description] if isinstance(description, str) else list(description)
    return ParsedTransaction(date.fromisoformat(day) if isinstance(day, str) else day, code, lines, amount, **kw)


def statement(txns: list[ParsedTransaction], start: str = "2026-06-01", end: str = "2026-06-30", *,
              identifier: str = JOINT_ID, name: str = JOINT_HOLDER, kind: str = "current",
              institution: str = "HSBC UK", parser: str = "hsbc_current", opening: float | None = None,
              closing: float | None = None, payments_in: float | None = None,
              payments_out: float | None = None) -> ParsedStatement:
    """A parsed statement, by default for the Riveras' joint HSBC account, with no balances."""
    return ParsedStatement(parser=parser, institution=institution, account_name=name, account_identifier=identifier,
                           account_kind=kind, period_start=date.fromisoformat(start),
                           period_end=date.fromisoformat(end), opening_balance=opening, closing_balance=closing,
                           payments_in=payments_in, payments_out=payments_out, transactions=list(txns))


def joint_statement(period: tuple[str, str] = ("2026-06-26", "2026-07-25")) -> ParsedStatement:
    """One reconciling joint-account statement: a transfer in, a supermarket shop, two identical unknown shops
    and an FX fee. Opens at £100.00, closes at £1,333.49."""
    return statement([
        txn("2026-06-27", ["S RIVERA", "JOINT ACCOUNT"], 1350.0, "CR", balance_after=1450.0),
        txn("2026-06-30", ["TESCO STORES 2041", "EXAMPLETOWN"], -96.40),
        txn("2026-06-30", ["UNKNOWN SHOP", "EXAMPLETOWN"], -10.0, "VIS"),
        txn("2026-06-30", ["UNKNOWN SHOP", "EXAMPLETOWN"], -10.0, "VIS"),
        txn("2026-07-01", ["NON-STERLING", "TRANSACTION FEE"], -0.11, "DR", balance_after=1333.49),
    ], *period, opening=100.0, closing=1333.49, payments_in=1350.0, payments_out=116.51)


# The rules that file joint_statement(): everything but the two unknown shops.
JOINT_STATEMENT_RULES = [
    rule("RIVERA", "Transfers/Between own accounts", priority=20),
    rule("TESCO", "Groceries/Supermarket"),
    rule("NON-STERLING", "Bank charges/FX fees", priority=5),
]


# ----------------------------------------------------------------- Monzo CSV
MONZO_CSV_HEADER = ("Transaction ID,Date,Time,Type,Name,Emoji,Category,Amount,Currency,Local amount,"
                    "Local currency,Notes and #tags,Address,Receipt,Description,Category split,Money Out,Money In\n")


# ------------------------------------------------------ generated statements
@dataclass
class DemoStatements:
    """The demo household and the statement files written for it."""
    household: demo_household.Household
    root: Path
    files: list[Path]

    def account(self, key: str) -> demo_household.Account:
        return self.household.accounts[key]

    def of(self, account_key: str) -> list[Path]:
        folder = self.root / self.account(account_key).folder
        return [p for p in self.files if p.parent == folder]


def write_demo_statements(root: Path, seed: int = DEMO_SEED, reference: date = DEMO_REFERENCE) -> DemoStatements:
    hh = demo_household.generate(seed, reference)
    return DemoStatements(household=hh, root=root, files=demo_writers.write_all(hh, root))


def demo_account(key: str = "test", fmt: str = "hsbc_pdf", opening: float = 0.0,
                 txns: list[tuple[date, float, list[str], str]] = ()) -> demo_household.Account:
    """A one-off fictional account for the demo writers: txns are (day, pounds, printed lines, type code)."""
    holder, sort_code, number = {
        "amex_pdf": ("ALEX RIVERA", "", "xxxx-xxxxxx-00000"),
        "barclays_pdf": (BARCLAYS_HOLDER, BARCLAYS_SORT_CODE, BARCLAYS_NUMBER),
    }.get(fmt, (JOINT_HOLDER, JOINT_SORT_CODE, JOINT_NUMBER))
    acct = demo_household.Account(
        key, "Test account", "alex", "credit_card" if fmt == "amex_pdf" else "current", fmt, f"alex/{key}",
        holder, sort_code, number, int(round(opening * 100)))
    acct.txns = [demo_household.Txn(d, int(round(pounds * 100)), list(lines), code) for d, pounds, lines, code in txns]
    return acct
