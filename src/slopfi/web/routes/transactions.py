"""Transactions: list with filters, categorise (single and bulk), one-off, CSV export."""
from __future__ import annotations

import csv
import io
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from ... import categorise, overview, reports, review
from ..app import Conn, _opt_int, hx_events, month_label, render, templates

router = APIRouter()
templates.env.filters.setdefault("money_pence", overview.money_pence)
templates.env.filters.setdefault("date_label", overview.date_label)

RESULTS = "txn-results"   # the id of the swapped region; htmx names it in HX-Target


def _filters(month, category_id, q, uncategorised, account_id) -> dict:
    return {"month": month or "all", "category_id": category_id or "", "q": (q or "").strip(),
            "uncategorised": "1" if uncategorised in ("1", "true", "on", True) else "", "account_id": account_id or ""}


def resolve_category(options: list[dict], text: str | None) -> tuple[int | None, str | None]:
    """A combobox value -> category id. Accepts the full label ('Shopping / Gifts'), a unique leaf ('Gifts'),
    a unique prefix, or an id; blank clears. Returns (id, None) or (None, error message)."""
    text = (text or "").strip()
    if not text:
        return None, None
    if text.isdigit() and any(o["id"] == int(text) for o in options):
        return int(text), None
    low = text.lower()
    for match in (
        [o for o in options if o["label"].lower() == low],
        [o for o in options if o["label"].split(" / ")[-1].lower() == low],
        [o for o in options if o["label"].lower().startswith(low)],
    ):
        if len(match) == 1:
            return match[0]["id"], None
        if len(match) > 1:
            return None, "More than one category matches; choose one from the list."
    return None, "Choose a category from the list."


def _result_line(filters: dict, rows: list, total: float, categories: list[dict], accounts: list) -> str:
    n = len(rows)
    what = "uncategorised" if filters["uncategorised"] else ("transaction" if n == 1 else "transactions")
    line = f"{n:,} {what}" if n else f"No {'uncategorised transactions' if filters['uncategorised'] else 'transactions'}"
    if filters["category_id"]:
        label = next((c["label"] for c in categories if str(c["id"]) == str(filters["category_id"])), None)
        if label:
            line += f" in {label}"
    if filters["q"]:
        line += f" matching “{filters['q']}”"
    line += f" in {month_label(filters['month'])}" if filters["month"] != "all" else " across all months"
    if filters["account_id"]:
        name = next((a["name"] for a in accounts if str(a["id"]) == str(filters["account_id"])), None)
        if name:
            line += f" from {name}"
    if n:
        line += f" · net {overview.money_pence(total, signed=True)}"
    return line


def _context(conn, filters: dict) -> dict:
    rows = reports.transactions(conn, month=filters["month"], category_id=_opt_int(filters["category_id"]),
                                q=filters["q"] or None, uncategorised=bool(filters["uncategorised"]),
                                account_id=_opt_int(filters["account_id"]))
    categories = reports.category_options(conn)
    accounts = reports.accounts(conn)
    total = sum(r["amount"] for r in rows)
    return {"rows": rows, "total": total, "filters": filters, "categories": categories, "accounts": accounts,
            "months": reports.months_available(conn), "query": urlencode(filters), "suggest": categorise.suggest_pattern,
            "show_accounts": len({a["owner"] for a in accounts}) > 1,
            "result_line": _result_line(filters, rows, total, categories, accounts),
            "has_any": bool(conn.execute("SELECT 1 FROM transactions LIMIT 1").fetchone())}


def _page(request: Request, conn, filters: dict, partial: bool) -> HTMLResponse:
    return render(request, "_txn_results.html" if partial else "transactions.html", **_context(conn, filters))


def _row(request: Request, conn, txn_id: int, error: str | None = None, value: str | None = None) -> HTMLResponse:
    return render(request, "_txn_row.html", r=reports.transaction(conn, txn_id), suggest=categorise.suggest_pattern,
                  error=error, value=value)


@router.get("/transactions", response_class=HTMLResponse)
def transactions(
    request: Request, conn: Conn,
    month: str | None = None, category_id: str | None = None, q: str | None = None,
    uncategorised: str | None = None, account_id: str | None = None,
):
    if not request.query_params:
        # The bare page opens on the month being closed (all of history is ~0.5MB of rows); "All time" stays in the
        # Month select, and any link that carries a filter but no month still means all months.
        month = review.latest_open_month(conn) or next(iter(reports.months_available(conn)), None)
    filters = _filters(month, category_id, q, uncategorised, account_id)
    partial = request.headers.get("HX-Target") == RESULTS and not request.headers.get("HX-History-Restore-Request")
    return _page(request, conn, filters, partial)


@router.post("/transactions/{txn_id:int}/category", response_class=HTMLResponse)
def set_transaction_category(
    request: Request, conn: Conn, txn_id: int,
    category: str = Form(""), category_id: str = Form(""), create_rule: str = Form(""), pattern: str = Form(""),
):
    """The row's combobox posts a label; an id is accepted too. Unknown text re-renders the row with the error."""
    if category_id:
        cat, error = int(category_id), None
    else:
        cat, error = resolve_category(reports.category_options(conn), category)
    if error:
        return _row(request, conn, txn_id, error=error, value=category)
    categorise.set_category(conn, txn_id, cat)
    rule_applied = 0
    if cat is not None and create_rule and pattern.strip():
        categorise.create_rule(conn, pattern=pattern, category_id=cat)
        rule_applied = categorise.apply_rules(conn)
    resp = _row(request, conn, txn_id)
    if rule_applied:
        resp.headers["HX-Refresh"] = "true"
    row = reports.transaction(conn, txn_id)
    label = row["category_name"] if row and row["category_id"] else None
    toast = f"Filed under {label}" if label else "Category cleared"
    if rule_applied:
        toast += f" · rule made, {rule_applied} more filed"
    return hx_events(resp, refresh=True, toast=toast)


@router.post("/transactions/{txn_id:int}/one_off", response_class=HTMLResponse)
def toggle_one_off(request: Request, conn: Conn, txn_id: int, one_off: str = Form("")):
    """The checkbox posts its value only when ticked, so a blank means off."""
    on = one_off in ("1", "on", "true")
    reports.set_one_off(conn, txn_id, on)
    return hx_events(_row(request, conn, txn_id), refresh=True, toast="Marked one-off" if on else "One-off removed")


# ------------------------------------------------------------------- bulk
def _bulk_filters(month, category_id, q, uncategorised, account_id) -> dict:
    return _filters(month or "all", category_id, q, uncategorised, account_id)


@router.post("/transactions/bulk/category", response_class=HTMLResponse)
def bulk_category(
    request: Request, conn: Conn, ids: Annotated[list[str], Form()] = [], category: str = Form(""),
    month: str = Form(""), category_id: str = Form(""), q: str = Form(""), uncategorised: str = Form(""),
    account_id: str = Form(""),
):
    """Apply one category to every selected row, then re-render the results under the current filters."""
    filters = _bulk_filters(month, category_id, q, uncategorised, account_id)
    txn_ids = [int(i) for i in ids if i.isdigit()]
    options = reports.category_options(conn)
    cat, error = resolve_category(options, category)
    if not txn_ids:
        return hx_events(_page(request, conn, filters, True), toast="Select at least one transaction.", kind="error")
    if error or cat is None:
        return hx_events(_page(request, conn, filters, True), toast=error or "Choose a category to apply.", kind="error")
    for txn_id in txn_ids:
        categorise.set_category(conn, txn_id, cat)
    label = next(o["label"] for o in options if o["id"] == cat).split(" / ")[-1]
    return hx_events(_page(request, conn, filters, True), refresh=True, toast=f"Filed {len(txn_ids)} under {label}")


@router.post("/transactions/bulk/one_off", response_class=HTMLResponse)
def bulk_one_off(
    request: Request, conn: Conn, ids: Annotated[list[str], Form()] = [], one_off: str = Form("1"),
    month: str = Form(""), category_id: str = Form(""), q: str = Form(""), uncategorised: str = Form(""),
    account_id: str = Form(""),
):
    filters = _bulk_filters(month, category_id, q, uncategorised, account_id)
    txn_ids = [int(i) for i in ids if i.isdigit()]
    if not txn_ids:
        return hx_events(_page(request, conn, filters, True), toast="Select at least one transaction.", kind="error")
    on = one_off in ("1", "on", "true")
    for txn_id in txn_ids:
        reports.set_one_off(conn, txn_id, on)
    return hx_events(_page(request, conn, filters, True), refresh=True,
                     toast=f"Marked {len(txn_ids)} one-off" if on else f"One-off removed from {len(txn_ids)}")


# ----------------------------------------------------------------- export
@router.get("/export/transactions.csv")
def export_transactions(
    conn: Conn, month: str | None = None, category_id: str | None = None, q: str | None = None,
    uncategorised: str | None = None, account_id: str | None = None,
):
    category_id, account_id = _opt_int(category_id), _opt_int(account_id)
    uncategorised = uncategorised in ("1", "true", "on")
    rows = reports.transactions(conn, month=month or "all", category_id=category_id, q=q,
                                uncategorised=uncategorised, account_id=account_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "account", "type", "description", "amount", "category", "subcategory", "categorised_by", "one_off"])
    for r in rows:
        w.writerow([r["date"], r["account_name"], r["type_code"], r["description"], f"{r['amount']:.2f}",
                    r["parent_name"] or r["category_name"] or "", r["category_name"] if r["parent_name"] else "",
                    r["categorised_by"] or "", "1" if r["one_off"] else ""])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=transactions.csv"})
