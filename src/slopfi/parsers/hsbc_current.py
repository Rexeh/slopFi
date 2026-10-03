"""Parser for HSBC UK current account PDF statements.

Uses PyMuPDF word coordinates: columns are identified by x-position rather than
whitespace, so multi-line descriptions and right-aligned amounts are unambiguous.

Layout facts (A4, points):
  date column   x0 <  100   e.g. "25 Jun 26" - only on the first row of each day
  type column   105-135     CR DR DD SO VIS ))) ATM BP ...
  details       135-340     1..5 lines per transaction
  amounts       x0 >= 340   right-aligned under "Paid out" / "Paid in" / "Balance";
                            balance only on the last row of each day; "D" suffix = overdrawn
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pymupdf

from . import ParsedStatement, ParsedTransaction, ParseError

PARSER_NAME = "hsbc_current"

DATE_RE = re.compile(r"^(\d{2}) ([A-Z][a-z]{2}) (\d{2})$")
AMOUNT_RE = re.compile(r"^(\d{1,3}(?:,\d{3})*|\d+)\.\d{2}$")
FX_RE = re.compile(r"^([A-Z]{3}) ([\d,]+\.\d{2}) @ ([\d.]+)$")
PERIOD_RE = re.compile(r"(\d{1,2} [A-Z][a-z]+(?: \d{4})?) to (\d{1,2} [A-Z][a-z]+ \d{4})")
SORT_CODE_RE = re.compile(r"^\d{2}-\d{2}-\d{2}$")
ACCOUNT_NO_RE = re.compile(r"^\d{8}$")
KNOWN_TYPES = {"CR", "DR", "DD", "SO", "VIS", ")))", "ATM", "BP", "TFR", "CHQ", "CHG", "SOL", "OTR"}
ROW_TOLERANCE = 3.0


@dataclass(frozen=True)
class Word:
    x0: float
    x1: float
    text: str


@dataclass
class Row:
    y: float
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class Columns:
    date_max: float = 100.0
    type_max: float = 135.0
    amount_min: float = 340.0
    out_right: float = 389.0
    in_right: float = 471.0
    bal_right: float = 550.0


def _page_rows(page) -> list[Row]:
    words = sorted(
        (Word(w[0], w[2], w[4]) for w in page.get_text("words")),
        key=lambda w: w.x0,
    )
    raw = sorted(page.get_text("words"), key=lambda w: (w[1], w[0]))
    rows: list[Row] = []
    for x0, y0, x1, _y1, text, *_ in raw:
        if rows and abs(rows[-1].y - y0) <= ROW_TOLERANCE:
            rows[-1].words.append(Word(x0, x1, text))
        else:
            rows.append(Row(y0, [Word(x0, x1, text)]))
    for row in rows:
        row.words.sort(key=lambda w: w.x0)
    del words
    return rows


def _find_header(rows: list[Row]) -> tuple[int, Columns] | None:
    for i, row in enumerate(rows):
        texts = [w.text for w in row.words]
        if texts and texts[0] == "Date" and row.words[0].x0 < 80 and "Balance" in texts:
            cols = Columns()
            for j, w in enumerate(row.words):
                prev = row.words[j - 1].text if j else ""
                if w.text == "out" and prev == "Paid":
                    cols.out_right = w.x1
                elif w.text == "in" and prev == "Paid":
                    cols.in_right = w.x1
                elif w.text == "Balance":
                    cols.bal_right = w.x1
            return i, cols
    return None


def _money(token: str) -> float | None:
    t = token.replace("£", "").replace(",", "")
    neg = t.startswith("-")
    t = t.lstrip("-")
    if not re.fullmatch(r"\d+\.\d{2}", t):
        return None
    return -float(t) if neg else float(t)


def _parse_date(text: str) -> date:
    return datetime.strptime(text, "%d %b %y").date()


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


def sniff(path: str | Path) -> bool:
    with pymupdf.open(path) as doc:
        if len(doc) == 0:
            return False
        text = doc[0].get_text()
        return "hsbc.co.uk" in text.lower() and "Your Statement" in text and "Sortcode" in text.replace(" ", "")


def _summary(rows: list[Row]) -> dict[str, float | None]:
    """Pull the Account Summary block off page 1."""
    labels = {
        ("Opening", "Balance"): "opening_balance",
        ("Payments", "In"): "payments_in",
        ("Payments", "Out"): "payments_out",
        ("Closing", "Balance"): "closing_balance",
    }
    out: dict[str, float | None] = {v: None for v in labels.values()}
    for row in rows:
        texts = [w.text for w in row.words]
        for key, name in labels.items():
            if tuple(texts[:2]) == key:
                value = None
                for tok in texts[2:]:
                    v = _money(tok)
                    if v is not None:
                        value = v
                    elif tok == "D" and value is not None:
                        value = -value
                out[name] = value
    return out


def _account_details(rows: list[Row]) -> tuple[str, str, str]:
    """Return (account name, sort code, account number) from the page-1 header."""
    for i, row in enumerate(rows):
        texts = [w.text for w in row.words]
        if texts[:2] == ["Account", "Name"] and i + 1 < len(rows):
            nxt = rows[i + 1]
            name = " ".join(w.text for w in nxt.words if w.x0 < 340)
            sort_code = next((w.text for w in nxt.words if SORT_CODE_RE.match(w.text)), "")
            acct = next((w.text for w in nxt.words if ACCOUNT_NO_RE.match(w.text)), "")
            return name, sort_code, acct
    raise ParseError("account details not found")


def parse(path: str | Path) -> ParsedStatement:
    path = Path(path)
    with pymupdf.open(path) as doc:
        if len(doc) == 0:
            raise ParseError("empty PDF")
        first_rows = _page_rows(doc[0])
        period_start, period_end = _parse_period(doc[0].get_text())
        summary = _summary(first_rows)
        account_name, sort_code, account_no = _account_details(first_rows)

        stmt = ParsedStatement(
            parser=PARSER_NAME,
            institution="HSBC UK",
            account_name=account_name,
            account_identifier=f"{sort_code} {account_no}".strip(),
            account_kind="current",
            period_start=period_start,
            period_end=period_end,
            **summary,
        )

        state = _State(stmt)
        for page_no, page in enumerate(doc, start=1):
            rows = _page_rows(page)
            header = _find_header(rows)
            if header is None:
                continue  # terms & conditions / info pages
            start, cols = header
            for row in rows[start + 1:]:
                if row.text.startswith("Customer Service Centre"):
                    break
                if state.feed(row, cols, page_no):
                    break  # BALANCE CARRIED FORWARD: nothing transactional follows on this page
        state.finish()
    return stmt


class _State:
    def __init__(self, stmt: ParsedStatement):
        self.stmt = stmt
        self.current_date: date | None = None
        self.txn: ParsedTransaction | None = None

    def _push(self) -> None:
        if self.txn is not None:
            self.stmt.transactions.append(self.txn)
            self.txn = None

    def feed(self, row: Row, cols: Columns, page_no: int) -> bool:
        """Consume one row. Returns True when the row was BALANCE CARRIED FORWARD."""
        date_words, type_words, detail_words, amount_words = [], [], [], []
        for w in row.words:
            if w.x0 < cols.date_max:
                date_words.append(w.text)
            elif w.x0 < cols.type_max:
                type_words.append(w.text)
            elif w.x0 < cols.amount_min:
                detail_words.append(w.text)
            else:
                amount_words.append(w)

        date_text = " ".join(date_words)
        if DATE_RE.match(date_text):
            self.current_date = _parse_date(date_text)
        elif date_words:
            return False  # header fragments such as the stray "A" marker

        detail = " ".join(t for t in detail_words if t != ".").strip()
        if detail.startswith("BALANCE BROUGHT FORWARD"):
            self._push()
            return False
        if detail.startswith("BALANCE CARRIED FORWARD"):
            self._push()
            return True

        if type_words:
            self._push()
            code = " ".join(type_words)
            if code not in KNOWN_TYPES:
                self.stmt.warnings.append(f"page {page_no}: unknown payment type {code!r} ({detail!r})")
            if self.current_date is None:
                raise ParseError(f"page {page_no}: transaction before any date: {detail!r}")
            self.txn = ParsedTransaction(date=self.current_date, type_code=code, lines=[])

        if detail:
            if self.txn is None:
                self.stmt.warnings.append(f"page {page_no}: orphan text {detail!r}")
            else:
                self.txn.lines.append(detail)
                fx = FX_RE.match(detail)
                if fx:
                    self.txn.fx_currency = fx.group(1)
                    self.txn.fx_amount = float(fx.group(2).replace(",", ""))
                    self.txn.fx_rate = float(fx.group(3))

        overdrawn = any(w.text == "D" for w in amount_words)
        for w in amount_words:
            value = _money(w.text)
            if value is None:
                continue
            distances = {
                "out": abs(w.x1 - cols.out_right),
                "in": abs(w.x1 - cols.in_right),
                "bal": abs(w.x1 - cols.bal_right),
            }
            column = min(distances, key=distances.get)
            if distances[column] > 8:
                self.stmt.warnings.append(f"page {page_no}: amount {w.text} at x={w.x1:.0f} not aligned to a column")
            if self.txn is None:
                self.stmt.warnings.append(f"page {page_no}: amount {w.text} with no transaction")
                continue
            if column == "bal":
                self.txn.balance_after = -value if overdrawn else value
            else:
                if self.txn.amount is not None:
                    raise ParseError(f"page {page_no}: two amounts for {self.txn.description!r}")
                self.txn.amount = -value if column == "out" else value
        return False

    def finish(self) -> None:
        self._push()
        problems = self.stmt.reconcile()
        if problems:
            raise ParseError("statement does not reconcile:\n  " + "\n  ".join(problems))
