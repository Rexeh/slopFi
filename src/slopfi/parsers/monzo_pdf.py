"""Parser for Monzo PDF statements.

One PDF bundles a current-account statement followed by one statement per pot, each with its
own header (balance, total outgoings, total deposits, pot name/type), then an FSCS page. Each
section becomes its own ParsedStatement; pots are savings sub-accounts of the main account.

Transaction rows are newest first with a running balance. A description can wrap onto the row
above and the row below the dated row (e.g. "ACME ANALYTICS LTD (Direct Credit)" / date+amount /
"Reference: A RIVERA"), so continuation rows within ~12pt of a dated row are attached to it.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pymupdf

from . import ParsedStatement, ParsedTransaction, ParseError, group_rows

PARSER_NAME = "monzo_pdf"
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
MONEY_RE = re.compile(r"^[+-]?£?-?[\d,]*\d\.\d{2}$")
SECTION_TITLES = ("Personal Account statement", "Joint Account statement", "Pot statement")
TYPE_MARKERS = {
    "(Direct Debit)": "DD", "(Direct Credit)": "BACS", "(Faster Payments)": "FP",
    "(P2P Payment)": "M2M", "(Standing Order)": "SO", "(Bank Transfer)": "TFR",
}
DESC_MIN, AMOUNT_MIN, BALANCE_MIN = 140.0, 370.0, 455.0
ATTACH_DISTANCE = 16.0   # wrapped lines sit 7pt away, FX lines up to 14pt; next transaction is ~30pt


def _money(tok: str) -> float | None:
    t = tok.replace("£", "").replace(",", "").replace("+", "")
    try:
        return float(t) if re.fullmatch(r"-?\d+\.\d{2}", t) else None
    except ValueError:
        return None


def sniff(path: str | Path) -> bool:
    if Path(path).suffix.lower() != ".pdf":
        return False
    with pymupdf.open(path) as doc:
        text = doc[0].get_text() if len(doc) else ""
        return "monzo" in text.lower() and any(t in text for t in SECTION_TITLES)


def _section_header(rows) -> dict:
    """Read the labelled values at the top of a section page. Values sit on the row above their label."""
    out: dict = {}

    def value_above(i: int) -> str:
        """Nearest preceding row (up to 3 back) with right-hand text: the value for a label on row i."""
        for j in range(i - 1, max(i - 4, -1), -1):
            right = [w[2] for w in rows[j][1] if w[0] > 300]
            if right:
                return " ".join(right)
        return ""

    for i, (_y, words) in enumerate(rows):
        text = " ".join(w[2] for w in words if w[0] > 300)
        prev_text = value_above(i)
        if text == "Total balance":
            out["total_balance"] = _money(prev_text)
        elif text in ("Personal Account balance", "Joint Account balance"):
            out["account_balance"] = _money(prev_text)
        elif text == "Pot balance":
            out["account_balance"] = _money(prev_text)
        elif text == "Total outgoings":
            out["outgoings"] = abs(_money(prev_text) or 0.0)
        elif text == "Total deposits":
            out["deposits"] = _money(prev_text) or 0.0
        elif text == "Pot name":
            out["pot_name"] = prev_text
        elif text == "Pot type":
            out["pot_type"] = prev_text
        full = " ".join(w[2] for w in words)
        left = " ".join(w[2] for w in words if w[0] < 300)
        m = re.search(r"(\d{2}/\d{2}/\d{4}) - (\d{2}/\d{2}/\d{4})", full)
        if m and "period" not in out:
            out["period"] = (datetime.strptime(m.group(1), "%d/%m/%Y").date(), datetime.strptime(m.group(2), "%d/%m/%Y").date())
        m = re.match(r"^Sort code: (\d{2}-\d{2}-\d{2})", left)
        if m:
            out["sort_code"] = m.group(1)
        m = re.match(r"^Account number: (\d{8})", left)
        if m:
            out["account_no"] = m.group(1)
        if any(t == full for t in SECTION_TITLES):
            out["title"] = full
        if full.startswith("Date") and "Balance" in full and words[0][0] < 100:
            out["table_row"] = i
    return out


def _classify(desc: str, pot: bool) -> tuple[str, list[str], dict]:
    """Split a raw description into (type_code, lines, fx)."""
    code = "CARD"
    for marker, c in TYPE_MARKERS.items():
        if marker in desc:
            code = c
            desc = desc.replace(marker, "")
    fx: dict = {}
    m = re.search(r"Amount: ([A-Z]{3}) (-?[\d,]+\.\d{2})\.?", desc)
    if m:
        fx = {"fx_currency": m.group(1), "fx_amount": abs(float(m.group(2).replace(",", "")))}
        desc = desc.replace(m.group(0), "")
    m = re.search(r"\.?\s*Exchange rate:\s*([\d.]*\d)\.?", desc)
    if m:
        if m.group(1):
            fx["fx_rate"] = float(m.group(1))
        desc = desc.replace(m.group(0), "")
    desc = desc.replace("This relates to a previous transaction", "")
    desc = re.sub(r"\s+", " ", desc).strip()
    lines: list[str] = []
    if "Reference:" in desc:
        head, ref = desc.split("Reference:", 1)
        lines = [head.strip(), ref.strip()] if head.strip() else [ref.strip()]
    else:
        lines = [desc]
    lead = lines[0]
    if pot:
        code = "INT" if lead.startswith("Interest for") else "POT"
    elif lead.startswith("Transfer to Pot") or lead.startswith("Transfer from Pot"):
        code = "POT"
    elif lead == "Monzo Plus" or lead.endswith("Monzo Plus"):
        code = "FEE"
    elif "overdraft fees" in lead:
        code = "CHG"
    elif lead.startswith("Interest for"):
        code = "INT"
    return code, [l for l in lines if l], fx


def _parse_rows(section_pages, pot: bool, warnings: list[str]) -> list[ParsedTransaction]:
    """Collect transactions (newest first as printed) from the pages of one section."""
    printed: list[tuple[date, str, float, float]] = []
    for rows, start_index in section_pages:
        body = []
        for y, words in rows[start_index + 1:]:
            if words and words[0][2] == "Monzo" and words[0][0] < 80 and any(w[2] == "Limited" for w in words):
                break  # page footer
            body.append((y, words))
        anchors = [(i, y, words) for i, (y, words) in enumerate(body)
                   if words[0][0] < 100 and DATE_RE.match(words[0][2])]
        used: set[int] = set()
        for i, y, words in anchors:
            d = datetime.strptime(words[0][2], "%d/%m/%Y").date()
            amount = balance = None
            for w in words:
                if w[0] >= BALANCE_MIN:
                    balance = _money(w[2])
                elif w[0] >= AMOUNT_MIN:
                    amount = _money(w[2])
            parts = [(y, " ".join(w[2] for w in words if DESC_MIN <= w[0] < AMOUNT_MIN))]
            for j in (i - 1, i + 1):
                if 0 <= j < len(body) and j not in used and (j, ) and not (body[j][1][0][0] < 100 and DATE_RE.match(body[j][1][0][2])):
                    jy, jwords = body[j]
                    if abs(jy - y) <= ATTACH_DISTANCE and all(w[0] >= DESC_MIN for w in jwords):
                        parts.append((jy, " ".join(w[2] for w in jwords if w[0] < AMOUNT_MIN)))
                        used.add(j)
            # a wrapped description can take two rows below the date; pick those up too
            j = i + 2
            if j < len(body) and j not in used and (i + 1) in used:
                jy, jwords = body[j]
                if abs(jy - body[i + 1][0]) <= ATTACH_DISTANCE and all(w[0] >= DESC_MIN for w in jwords) \
                        and not DATE_RE.match(jwords[0][2]):
                    parts.append((jy, " ".join(w[2] for w in jwords if w[0] < AMOUNT_MIN)))
                    used.add(j)
            desc = " ".join(t for _y, t in sorted(parts) if t)
            if amount is None or balance is None:
                raise ParseError(f"row {d} {desc!r}: amount or balance missing")
            printed.append((d, desc, amount, balance))
    txns = []
    for d, desc, amount, balance in reversed(printed):   # chronological
        code, lines, fx = _classify(desc, pot)
        txns.append(ParsedTransaction(date=d, type_code=code, lines=lines or ["(no description)"],
                                      amount=amount, balance_after=balance, **fx))
    return txns


def parse_many(path: str | Path) -> list[ParsedStatement]:
    with pymupdf.open(path) as doc:
        pages = [group_rows(p) for p in doc]
    # split into sections at pages carrying a section title
    sections: list[dict] = []
    for rows in pages:
        header = _section_header(rows)
        if "title" in header:
            sections.append({"header": header, "pages": []})
        if not sections:
            continue
        if " ".join(w[2] for _y, ws in rows[:3] for w in ws).startswith("Important information"):
            break
        table_row = header.get("table_row")
        if table_row is not None:
            sections[-1]["pages"].append((rows, table_row))
    if not sections:
        raise ParseError("no Monzo statement sections found")

    main = sections[0]["header"]
    sort_code, account_no = main.get("sort_code", ""), main.get("account_no", "")
    if not (sort_code and account_no):
        raise ParseError("sort code / account number not found")
    period = main.get("period")
    if not period:
        raise ParseError("statement period not found")
    base_id = f"{sort_code} {account_no}"
    owner_hint = "joint" if main["title"].startswith("Joint") else "personal"

    out: list[ParsedStatement] = []
    for sec in sections:
        h = sec["header"]
        pot = h["title"] == "Pot statement"
        warnings: list[str] = []
        txns = _parse_rows(sec["pages"], pot, warnings)
        closing = h.get("account_balance")
        opening = (txns[0].balance_after - txns[0].amount) if txns else closing
        name = f"Monzo pot: {h.get('pot_name', '?')}" if pot else f"Monzo {owner_hint} account"
        stmt = ParsedStatement(
            parser=PARSER_NAME, institution="Monzo", account_name=name,
            account_identifier=f"{base_id} pot:{h.get('pot_name', '?')}" if pot else base_id,
            account_kind="savings" if pot else "current",
            period_start=period[0], period_end=period[1],
            opening_balance=round(opening, 2) if opening is not None else None, closing_balance=closing,
            payments_in=h.get("deposits"), payments_out=h.get("outgoings"),
            transactions=txns, warnings=warnings,
        )
        stmt.sub_account = h.get("pot_name") if pot else None
        problems = stmt.reconcile()
        if problems:
            raise ParseError(f"{name}: statement does not reconcile:\n  " + "\n  ".join(problems))
        out.append(stmt)
    return out


def parse(path: str | Path) -> ParsedStatement:
    return parse_many(path)[0]
