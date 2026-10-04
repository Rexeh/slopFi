"""Parser for Barclays UK current account PDF statements.

Uses PyMuPDF word coordinates, like the HSBC parser: columns are told apart by x-position.

Layout facts (A4, points):
  page 1        "At a glance" box at x > 400: Start balance, Money in, Money out, End balance (with a £ sign);
                the period "29 Feb - 31 Mar 2020", the holder, then "• Sort Code" and "• Account no." lines
  table header  "Date Description Money out Money in Balance"; amounts are right-aligned under the header words
  date column   x0 < 92   "02 Mar" (no year: taken from the period), first row of each day only
  description   92-255    wrapped mid-phrase at the column edge, several rows per transaction
  amounts       x0 >= 255 the amount sits on the transaction's first row; the balance is printed once per day,
                          on the first row of the day's last transaction
There is no type column: the type is the printed prefix ("Card Purchase", "Direct Debit to", ...), which
becomes the type code. "Ref: ...", "Timed at ..." and a card payment's "On dd Mon" become their own lines. A
foreign card purchase ("... EUR 3.50 On 26 Jun at VISA Exchange Rate 1.16 The Final GBP Amount Includes A
Non-Sterling Transaction Fee of £ 0.09") is split into the HSBC-style "EUR 3.50 @ 1.16" and fee lines.
Pages without the table header (terms, FSCS sheet) are skipped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pymupdf

from . import ParsedStatement, ParsedTransaction, ParseError

PARSER_NAME = "barclays_current"

DATE_RE = re.compile(r"^(\d{1,2}) ([A-Z][a-z]{2})$")
PERIOD_RE = re.compile(r"(\d{1,2} [A-Z][a-z]{2}) - (\d{1,2} [A-Z][a-z]{2} \d{4})")
SORT_CODE_RE = re.compile(r"^\d{2}-\d{2}-\d{2}$")
ACCOUNT_NO_RE = re.compile(r"^\d{8}$")
ON_DATE_RE = re.compile(r"^(.*?)\s+(On \d{1,2} [A-Z][a-z]{2})$")
MARKER_RE = re.compile(r"\b(?=Ref:|Timed at |This Is A New )")
FX_RE = re.compile(r"^([A-Z]{3}) ([\d,]+\.\d{2}) @ ([\d.]+)$")
FOREIGN_RE = re.compile(
    r"^(?P<payee>.+?) (?P<ccy>[A-Z]{3}) (?P<amount>[\d,]+\.\d{2}) (?P<on>On \d{1,2} [A-Z][a-z]{2}) at VISA Exchange "
    r"Rate (?P<rate>\d+\.\d+)(?: The Final GBP Amount Includes A Non-Sterling Transaction Fee of £ ?(?P<fee>\d+\.\d{2}))?$"
)
# The printed prefix for each type; statements from 2020 said "Card Payment to", 2026 ones say "Card Purchase".
PREFIXES = [
    ("Direct Debit to", "DD"), ("Card Payment to", "CARD"), ("Card Purchase", "CARD"), ("Received From", "CR"),
    ("Refund From", "CR"), ("Bill Payment to", "BP"), ("Payment to", "FP"), ("Cash Machine Withdrawal at", "ATM"),
    ("Standing Order to", "SO"), ("Transfer to", "TFR"), ("Transfer To", "TFR"), ("Transfer from", "TFR"),
    ("Transfer From", "TFR"),
]
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
    date_max: float = 92.0
    amount_min: float = 255.0
    out_right: float = 305.0
    in_right: float = 361.0
    bal_right: float = 414.0


def _page_rows(page) -> list[Row]:
    raw = sorted(page.get_text("words"), key=lambda w: (w[1], w[0]))
    rows: list[Row] = []
    for x0, y0, x1, _y1, text, *_ in raw:
        if rows and abs(rows[-1].y - y0) <= ROW_TOLERANCE:
            rows[-1].words.append(Word(x0, x1, text))
        else:
            rows.append(Row(y0, [Word(x0, x1, text)]))
    for row in rows:
        row.words.sort(key=lambda w: w.x0)
    return rows


def _money(token: str) -> float | None:
    t = token.replace("£", "").replace(",", "")
    neg = t.startswith("-")
    t = t.lstrip("-")
    if not re.fullmatch(r"\d+\.\d{2}", t):
        return None
    return -float(t) if neg else float(t)


def _parse_period(text: str) -> tuple[date, date]:
    m = PERIOD_RE.search(text)
    if not m:
        raise ParseError("statement period not found")
    end = datetime.strptime(m.group(2), "%d %b %Y").date()
    start = datetime.strptime(f"{m.group(1)} {end.year}", "%d %b %Y").date()
    if start > end:
        start = start.replace(year=end.year - 1)
    return start, end


def _txn_date(text: str, start: date, end: date) -> date:
    """A table date ("02 Mar") in the year that puts it inside the statement period."""
    d = datetime.strptime(f"{text} {end.year}", "%d %b %Y").date()
    if d > end:
        d = d.replace(year=end.year - 1)
    return d


def _classify(text: str) -> tuple[str | None, list[str]]:
    """Split a transaction's re-joined text into (type code, lines): the payee, then "On dd Mon" for a card
    payment, then each "Ref:" / "Timed at" / "This Is A New ..." fragment on its own."""
    code = None
    for prefix, c in PREFIXES:
        if text.startswith(prefix + " "):
            code, text = c, text[len(prefix) + 1:]
            break
    foreign = FOREIGN_RE.match(text)
    if foreign:
        lines = [foreign["payee"], foreign["on"], f"{foreign['ccy']} {foreign['amount']} @ {foreign['rate']}"]
        if foreign["fee"]:
            lines.append(f"Non-Sterling Transaction Fee £{foreign['fee']}")
        return code, lines
    parts = [p.strip() for p in MARKER_RE.split(text) if p.strip()]
    lines: list[str] = []
    for i, part in enumerate(parts):
        m = ON_DATE_RE.match(part) if i == 0 else None
        if m:
            lines.extend([m.group(1), m.group(2)])
        else:
            lines.append(part)
    return code, lines or [text]


def sniff(path: str | Path) -> bool:
    with pymupdf.open(path) as doc:
        if len(doc) == 0:
            return False
        text = doc[0].get_text()
        return "Barclays" in text and "Your transactions" in text and "Sort Code" in text and "At a glance" in text


def _find_header(rows: list[Row]) -> tuple[int, Columns] | None:
    for i, row in enumerate(rows):
        texts = [w.text for w in row.words]
        if texts and texts[0] == "Date" and row.words[0].x0 < 80 and "Balance" in texts:
            cols = Columns()
            for j, w in enumerate(row.words):
                prev = row.words[j - 1].text if j else ""
                if w.text == "out" and prev == "Money":
                    cols.out_right = w.x1
                elif w.text == "in" and prev == "Money":
                    cols.in_right = w.x1
                elif w.text == "Balance":
                    cols.bal_right = w.x1
            cols.amount_min = cols.out_right - 50
            return i, cols
    return None


def _summary(rows: list[Row]) -> dict[str, float | None]:
    """The At a glance box on page 1: labels at x > 400 followed by a £ amount."""
    labels = {("Start", "balance"): "opening_balance", ("Money", "in"): "payments_in",
              ("Money", "out"): "payments_out", ("End", "balance"): "closing_balance"}
    out: dict[str, float | None] = {v: None for v in labels.values()}
    for row in rows:
        words = [w for w in row.words if w.x0 > 400]
        texts = [w.text for w in words]
        for key, name in labels.items():
            if tuple(texts[:2]) == key:
                values = [v for v in (_money(t) for t in texts[2:]) if v is not None]
                out[name] = values[-1] if values else None
    return out


def _account_details(rows: list[Row]) -> tuple[str, str, str]:
    """(holder, sort code, account number) from the page-1 header: the holder sits on the row above the
    "• Sort Code" line."""
    sort_code = account_no = holder = ""
    for i, row in enumerate(rows):
        texts = [w.text for w in row.words if not (len(w.text) == 1 and not w.text.isalnum())]  # drop the bullet
        if texts[:2] == ["Sort", "Code"] and len(texts) > 2 and SORT_CODE_RE.match(texts[2]):
            sort_code = texts[2]
            if i:
                holder = " ".join(w.text for w in rows[i - 1].words if w.x0 > 400)
        elif texts[:2] == ["Account", "no."] and len(texts) > 2 and ACCOUNT_NO_RE.match(texts[2]):
            account_no = texts[2]
    if not (sort_code and account_no):
        raise ParseError("account details not found")
    return holder, sort_code, account_no


def parse(path: str | Path) -> ParsedStatement:
    path = Path(path)
    with pymupdf.open(path) as doc:
        if len(doc) == 0:
            raise ParseError("empty PDF")
        first_rows = _page_rows(doc[0])
        period_start, period_end = _parse_period(doc[0].get_text())
        summary = _summary(first_rows)
        holder, sort_code, account_no = _account_details(first_rows)

        stmt = ParsedStatement(
            parser=PARSER_NAME,
            institution="Barclays",
            account_name=holder,
            account_identifier=f"{sort_code} {account_no}",
            account_kind="current",
            period_start=period_start,
            period_end=period_end,
            **summary,
        )

        state = _State(stmt)
        done = False
        for page_no, page in enumerate(doc, start=1):
            rows = _page_rows(page)
            header = _find_header(rows)
            if header is None:
                continue  # terms, FSCS sheet, notices
            start, cols = header
            for row in rows[start + 1:]:
                if row.text.startswith("Continued"):
                    break
                if state.feed(row, cols, page_no):
                    done = True
                    break
            if done:
                break
        state.finish()
    return stmt


class _State:
    def __init__(self, stmt: ParsedStatement):
        self.stmt = stmt
        self.current_date: date | None = None
        self.txn: ParsedTransaction | None = None
        self.raw: list[str] = []

    def _push(self) -> None:
        if self.txn is not None:
            code, lines = _classify(" ".join(self.raw))
            self.txn.type_code = code
            self.txn.lines = lines
            for line in lines:
                fx = FX_RE.match(line)
                if fx:
                    self.txn.fx_currency = fx.group(1)
                    self.txn.fx_amount = float(fx.group(2).replace(",", ""))
                    self.txn.fx_rate = float(fx.group(3))
            self.stmt.transactions.append(self.txn)
            self.txn = None
            self.raw = []

    def feed(self, row: Row, cols: Columns, page_no: int) -> bool:
        """Consume one table row. Returns True on the End balance row."""
        date_words, detail_words, amount_words = [], [], []
        for w in row.words:
            if w.x0 < cols.date_max:
                date_words.append(w.text)
            elif w.x0 < cols.amount_min:
                detail_words.append(w.text)
            else:
                amount_words.append(w)

        date_text = " ".join(date_words)
        if DATE_RE.match(date_text):
            self.current_date = _txn_date(date_text, self.stmt.period_start, self.stmt.period_end)
        elif date_words:
            return False  # footer or notice text starting at the left margin

        detail = " ".join(detail_words).strip()
        if detail == "Start balance":
            self._push()
            return False
        if detail == "End balance":
            self._push()
            return True

        amounts: list[tuple[str, float]] = []
        for w in amount_words:
            value = _money(w.text)
            if value is None:
                continue
            distances = {"out": abs(w.x1 - cols.out_right), "in": abs(w.x1 - cols.in_right),
                         "bal": abs(w.x1 - cols.bal_right)}
            column = min(distances, key=distances.get)
            if distances[column] > 8:
                self.stmt.warnings.append(f"page {page_no}: amount {w.text} at x={w.x1:.0f} not aligned to a column")
            amounts.append((column, value))

        if any(col != "bal" for col, _ in amounts):
            self._push()
            if self.current_date is None:
                raise ParseError(f"page {page_no}: transaction before any date: {detail!r}")
            self.txn = ParsedTransaction(date=self.current_date, type_code=None, lines=[])
        if detail:
            if self.txn is None:
                self.stmt.warnings.append(f"page {page_no}: orphan text {detail!r}")
            else:
                self.raw.append(detail)
        for column, value in amounts:
            if self.txn is None:
                self.stmt.warnings.append(f"page {page_no}: amount {value:.2f} with no transaction")
            elif column == "bal":
                self.txn.balance_after = value
            elif self.txn.amount is not None:
                raise ParseError(f"page {page_no}: two amounts for {' '.join(self.raw)!r}")
            else:
                self.txn.amount = -value if column == "out" else value
        return False

    def finish(self) -> None:
        self._push()
        for t in self.stmt.transactions:
            if t.type_code is None:
                self.stmt.warnings.append(f"unknown payment type for {t.description!r}")
        problems = self.stmt.reconcile()
        if problems:
            raise ParseError("statement does not reconcile:\n  " + "\n  ".join(problems))
