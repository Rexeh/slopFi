"""Statement parsers. Each parser module exposes `PARSER_NAME`, `sniff(path)` and `parse(path)`."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


class ParseError(Exception):
    """The file could not be parsed into a consistent statement."""


@dataclass
class ParsedTransaction:
    date: date
    type_code: str | None
    lines: list[str]
    amount: float | None = None          # signed: negative = money leaving the family
    balance_after: float | None = None
    fx_currency: str | None = None
    fx_amount: float | None = None
    fx_rate: float | None = None
    external_id: str | None = None       # bank-provided stable id, used for de-duplication when present
    suggested_category: str | None = None  # the source's own category label, used only as a fallback

    @property
    def description(self) -> str:
        return " / ".join(line for line in self.lines if line)


@dataclass
class ParsedStatement:
    parser: str
    institution: str
    account_name: str
    account_identifier: str               # '' when the format does not identify the account
    account_kind: str                     # current | credit_card | savings
    period_start: date
    period_end: date
    opening_balance: float | None
    closing_balance: float | None
    payments_in: float | None
    payments_out: float | None
    transactions: list[ParsedTransaction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sub_account: str | None = None        # e.g. a pot name: a savings account that belongs to the main one

    @property
    def requires_account(self) -> bool:
        """True when the caller must say which account this belongs to."""
        return not self.account_identifier

    def reconcile(self, tolerance: float = 0.005) -> list[str]:
        """Return a list of reconciliation problems (empty = everything ties up)."""
        problems: list[str] = []
        total_in = sum(t.amount for t in self.transactions if t.amount and t.amount > 0)
        total_out = -sum(t.amount for t in self.transactions if t.amount and t.amount < 0)
        if self.payments_in is not None and abs(total_in - self.payments_in) > tolerance:
            problems.append(f"payments in: parsed {total_in:.2f} vs statement {self.payments_in:.2f}")
        if self.payments_out is not None and abs(total_out - self.payments_out) > tolerance:
            problems.append(f"payments out: parsed {total_out:.2f} vs statement {self.payments_out:.2f}")
        if self.opening_balance is not None and self.closing_balance is not None:
            expected = self.opening_balance + total_in - total_out
            if abs(expected - self.closing_balance) > tolerance:
                problems.append(
                    f"closing balance: computed {expected:.2f} vs statement {self.closing_balance:.2f}"
                )
            running = self.opening_balance
            for t in self.transactions:
                running += t.amount or 0.0
                if t.balance_after is not None and abs(running - t.balance_after) > tolerance:
                    problems.append(
                        f"running balance after {t.date} {t.description!r}: "
                        f"computed {running:.2f} vs statement {t.balance_after:.2f}"
                    )
                    running = t.balance_after  # resync so one error doesn't cascade
        for t in self.transactions:
            if t.amount is None:
                problems.append(f"transaction without amount: {t.date} {t.description!r}")
        return problems


def available_parsers():
    from . import amex_card, barclays_current, hsbc_current, monzo_csv, monzo_pdf

    return [hsbc_current, barclays_current, amex_card, monzo_pdf, monzo_csv]


def detect_parser(path: str | Path):
    for module in available_parsers():
        try:
            if module.sniff(path):
                return module
        except Exception:  # noqa: BLE001 - a sniff failure just means "not this parser"
            continue
    return None


def parse_file(path: str | Path) -> ParsedStatement:
    return parse_file_all(path)[0]


def parse_file_all(path: str | Path) -> list[ParsedStatement]:
    """All statements in a file: one for most formats, several for a Monzo PDF with pots."""
    module = detect_parser(path)
    if module is None:
        raise ParseError(f"no parser recognises {Path(path).name}")
    if hasattr(module, "parse_many"):
        return module.parse_many(path)
    return [module.parse(path)]


def group_rows(page, tolerance: float = 3.0) -> list[tuple[float, list[tuple[float, float, str]]]]:
    """Group a PyMuPDF page's words into rows by y position: [(y, [(x0, x1, text), ...]), ...]."""
    raw = sorted(page.get_text("words"), key=lambda w: (w[1], w[0]))
    rows: list[tuple[float, list[tuple[float, float, str]]]] = []
    for x0, y0, x1, _y1, text, *_ in raw:
        if rows and abs(rows[-1][0] - y0) <= tolerance:
            rows[-1][1].append((x0, x1, text))
        else:
            rows.append((y0, [(x0, x1, text)]))
    for _y, words in rows:
        words.sort(key=lambda w: w[0])
    return rows
