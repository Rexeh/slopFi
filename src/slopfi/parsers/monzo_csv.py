"""Parser for Monzo CSV exports (personal and joint accounts).

The export has no balances or statement totals, so nothing can be reconciled, and it does not
say which account it came from: the importer must be told the account. Transaction IDs are
stable, so overlapping exports de-duplicate cleanly.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from . import ParsedStatement, ParsedTransaction, ParseError

PARSER_NAME = "monzo_csv"
HEADER_PREFIX = ["Transaction ID", "Date", "Time", "Type", "Name"]
TYPE_CODES = {
    "Card payment": "CARD",
    "Pot transfer": "POT",
    "Faster payment": "FP",
    "Direct Debit": "DD",
    "Bacs (Direct Credit)": "BACS",
    "Monzo-to-Monzo": "M2M",
    "Bank transfer": "TFR",
    "monzo_paid": "FEE",
    "overdraft": "CHG",
    "pot-savings": "INT",      # interest paid into a pot (pot exports only)
}


def _open(path):
    return open(path, newline="", encoding="utf-8-sig")


def sniff(path: str | Path) -> bool:
    if Path(path).suffix.lower() != ".csv":
        return False
    with _open(path) as f:
        header = next(csv.reader(f), [])
    return header[: len(HEADER_PREFIX)] == HEADER_PREFIX


def _clean(s: str) -> str:
    return " ".join(s.split())


def parse(path: str | Path) -> ParsedStatement:
    with _open(path) as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ParseError("empty Monzo export")
    txns: list[ParsedTransaction] = []
    for r in rows:
        try:
            d = datetime.strptime(r["Date"], "%d/%m/%Y").date()
            amount = float(r["Amount"])
        except (KeyError, ValueError) as exc:
            raise ParseError(f"bad row {r.get('Transaction ID')}: {exc}") from exc
        name, desc, notes = _clean(r.get("Name", "")), _clean(r.get("Description", "")), _clean(r.get("Notes and #tags", ""))
        if r.get("Type") == "Pot transfer" and name.lower() in ("pot", ""):
            name = "Pot transfer"   # pot-side export: the counterparty is the main account
        lines = [name] if name else []
        if desc and desc.upper() != name.upper():
            lines.append(desc)
        if notes and notes not in lines:
            lines.append(notes)
        if not lines:
            lines = [r.get("Type", "transaction")]
        t = ParsedTransaction(
            date=d, type_code=TYPE_CODES.get(r.get("Type", ""), (r.get("Type") or "OTH")[:5].upper()),
            lines=lines, amount=amount, external_id=r.get("Transaction ID") or None,
            suggested_category=r.get("Category") or None,
        )
        local_ccy = r.get("Local currency") or "GBP"
        if local_ccy != (r.get("Currency") or "GBP"):
            try:
                local = float(r.get("Local amount") or 0)
                t.fx_currency, t.fx_amount = local_ccy, abs(local)
                t.fx_rate = round(abs(local) / abs(amount), 4) if amount else None
            except ValueError:
                pass
        txns.append(t)
    txns.sort(key=lambda t: t.date)
    return ParsedStatement(
        parser=PARSER_NAME, institution="Monzo", account_name="Monzo", account_identifier="",
        account_kind="savings" if all(t.type_code in ("POT", "INT") for t in txns) else "current",
        period_start=txns[0].date, period_end=txns[-1].date,
        opening_balance=None, closing_balance=None, payments_in=None, payments_out=None,
        transactions=txns,
    )
