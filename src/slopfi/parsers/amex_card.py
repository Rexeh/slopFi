"""Parser for American Express UK credit card PDF statements.

Layout (points): transaction date x0 < 30 ("Jun 17"), process date ~58, details from x0=101,
foreign spend amount right-aligned near x1=417 on its own row, sterling amount right-aligned at
x1=533. A "CR" token under the amount on the following row marks a credit (payment or refund).

Sign convention: spend is negative (money leaving the family); payments received are positive.
Balances are stored from the family's point of view, so an amount owed is negative.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pymupdf

from . import ParsedStatement, ParsedTransaction, ParseError, group_rows

PARSER_NAME = "amex_card"
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
PERIOD_RE = re.compile(r"From (\d{1,2} [A-Z][a-z]+(?: \d{4})?) to (\d{1,2} [A-Z][a-z]+ \d{4})")
MEMBER_RE = re.compile(r"x{4}-x{6}-(\d{5})")
FX_RE = re.compile(r"Exchange Rate ([\d.]+) \+ Nonsterling Transaction Fee ([\d.]+)")
MONEY_RE = re.compile(r"^-?[\d,]*\d\.\d{2}$")

DETAILS_MIN = 95.0
FOREIGN_MIN = 340.0
AMOUNT_MIN = 470.0


def _money(tok: str) -> float | None:
    t = tok.replace("£", "")
    if re.fullmatch(r"\d+,\d{2}", t):   # euro-style decimal comma on foreign amounts ("11,26")
        t = t.replace(",", ".")
    t = t.replace(",", "")
    if t.startswith("."):
        t = "0" + t
    return float(t) if MONEY_RE.match(t) else None


def sniff(path: str | Path) -> bool:
    if Path(path).suffix.lower() != ".pdf":
        return False
    with pymupdf.open(path) as doc:
        text = doc[0].get_text() if len(doc) else ""
        return "americanexpress" in text.lower() and "Statement of Account" in text


def _parse_period(text: str) -> tuple[date, date]:
    m = PERIOD_RE.search(text)
    if not m:
        raise ParseError("statement period not found")
    end = datetime.strptime(m.group(2), "%d %B %Y").date()
    start_text = m.group(1)
    if not re.search(r"\d{4}$", start_text):
        start_text = f"{start_text} {end.year}"
    start = datetime.strptime(start_text, "%d %B %Y").date()
    if start > end:
        start = start.replace(year=end.year - 1)
    return start, end


def _summary(rows) -> tuple[float, float, float, float]:
    """Previous closing balance, new credits, new debits, closing balance (all as owed amounts)."""
    for i, (_y, words) in enumerate(rows):
        texts = [w[2] for w in words]
        if texts[:3] == ["Previous", "Closing", "Balance"] and i + 1 < len(rows):
            values: list[float] = []
            tokens = [w[2] for w in rows[i + 1][1]]
            for j, tok in enumerate(tokens):
                v = _money(tok)
                if v is None:
                    continue
                if j + 1 < len(tokens) and tokens[j + 1] == "CR":
                    v = -v
                values.append(v)
            if len(values) == 4:
                return values[0], values[1], values[2], values[3]
            raise ParseError(f"could not read account summary: {tokens}")
    raise ParseError("account summary not found")


def _txn_date(mon: str, day: str, start: date, end: date) -> date:
    month = MONTHS.get(mon)
    if month is None:
        raise ParseError(f"bad month {mon!r}")
    d = date(end.year, month, int(day))
    if d > end and start.year < end.year:
        d = d.replace(year=start.year)
    return d


def parse(path: str | Path) -> ParsedStatement:
    with pymupdf.open(path) as doc:
        rows1 = group_rows(doc[0])
        page1 = "\n".join(" ".join(w[2] for w in words) for _y, words in rows1)
        start, end = _parse_period(page1)
        m = MEMBER_RE.search(page1)
        identifier = f"…{m.group(1)}" if m else ""
        prev, credits, debits, closing = _summary(rows1)
        name = ""
        for i, (_y, words) in enumerate(rows1):
            if [w[2] for w in words][:2] == ["Prepared", "for"] and i + 1 < len(rows1):
                name = " ".join(w[2] for w in rows1[i + 1][1] if w[0] < 280)
                break

        stmt = ParsedStatement(
            parser=PARSER_NAME, institution="American Express", account_name=name,
            account_identifier=identifier, account_kind="credit_card",
            period_start=start, period_end=end,
            opening_balance=-prev, closing_balance=-closing, payments_in=credits, payments_out=debits,
        )

        txn: ParsedTransaction | None = None
        finished = False
        for page_no, page in enumerate(doc, start=1):
            rows = group_rows(page)
            header = next((i for i, (_y, ws) in enumerate(rows)
                           if ws[0][2] == "Date" and ws[0][0] < 30 and any(w[2] == "Amount" for w in ws)), None)
            if header is None:
                continue
            for _y, words in rows[header + 1:]:
                text = " ".join(w[2] for w in words)
                if text.startswith("Total new spend"):
                    finished = True
                    break
                if text.startswith("How you can pay"):
                    break  # footer of this page; transactions continue on the next
                first_x0, _x1, first = words[0]
                if first_x0 < 30 and first in MONTHS and len(words) >= 4:
                    if txn is not None:
                        stmt.transactions.append(txn)
                    day = words[1][2]
                    details = [w[2] for w in words[4:] if DETAILS_MIN <= w[0] < FOREIGN_MIN]
                    amount = None
                    for w in words:
                        if w[0] >= AMOUNT_MIN:
                            amount = _money(w[2])
                    if amount is None:
                        raise ParseError(f"page {page_no}: no amount on row {text!r}")
                    txn = ParsedTransaction(date=_txn_date(first, day, start, end), type_code="CARD",
                                            lines=[" ".join(details)], amount=-amount)
                    for w in words:
                        if FOREIGN_MIN <= w[0] < AMOUNT_MIN and _money(w[2]) is not None:
                            txn.fx_amount = _money(w[2])
                    continue
                if txn is None:
                    continue
                # continuation rows for the current transaction
                if any(w[2] == "CR" and w[0] >= AMOUNT_MIN for w in words):
                    txn.amount = -txn.amount
                    txn.type_code = "CR"
                    continue
                fx = FX_RE.search(text)
                if fx:
                    txn.fx_rate = float(fx.group(1))
                    txn.lines.append(text)
                    continue
                foreign = [w for w in words if FOREIGN_MIN <= w[0] < AMOUNT_MIN]
                if foreign and all(_money(w[2]) is not None for w in foreign) and len(words) == len(foreign):
                    txn.fx_amount = _money(foreign[0][2])
                    continue
                if foreign and all(w[0] >= FOREIGN_MIN for w in words):
                    txn.fx_currency = " ".join(w[2] for w in foreign)
                    continue
                extra = [w[2] for w in words if DETAILS_MIN <= w[0] < FOREIGN_MIN]
                if extra and all(w[0] >= DETAILS_MIN for w in words):
                    txn.lines.append(" ".join(extra))
            if finished:
                break
        if txn is not None:
            stmt.transactions.append(txn)

    problems = stmt.reconcile()
    if problems:
        raise ParseError("statement does not reconcile:\n  " + "\n  ".join(problems))
    return stmt
