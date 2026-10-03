"""Transactions: list with filters, categorise (single and bulk), one-off, CSV export."""
from __future__ import annotations

import csv
import io
from typing import Annotated
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from ... import categorise, overview, reports, review
from ..app import Conn, _opt_int, flash, hx_events, month_label, render, templates

router = APIRouter()
templates.env.filters.setdefault("money_pence", overview.money_pence)
templates.env.filters.setdefault("date_label", overview.date_label)

RESULTS = "txn-results"   # the id of the swapped region; htmx names it in HX-Target
SORT_FIRST = {"date": "desc", "amount": "asc", "one_off": "desc"}   # a header's first click; the rest open A–Z


def _sort(sort, dir) -> tuple[str, str]:
    """?sort=&dir= -> a known column and asc/desc, or ("", "") for the default, newest first."""
    if sort not in reports.TXN_SORTS:
        return "", ""
    dir = dir if dir in ("asc", "desc") else SORT_FIRST.get(sort, "asc")
    return ("", "") if (sort, dir) == ("date", "desc") else (sort, dir)


def _filters(month, category_id, q, uncategorised, account_id, sort=None, dir=None) -> dict:
    sort, dir = _sort(sort, dir)
    return {"month": month or "all", "category_id": category_id or "", "q": (q or "").strip(),
            "uncategorised": "1" if uncategorised in ("1", "true", "on", True) else "", "account_id": account_id or "",
            "sort": sort, "dir": dir}


def _sort_heads(filters: dict) -> dict:
    """Per sortable column: aria-sort when it is the active one, and the sort its header link asks for next."""
    active, current = filters["sort"] or "date", filters["dir"] or "desc"
    heads = {}
    for key in reports.TXN_SORTS:
        on = key == active
        sort, dir = _sort(key, ("asc" if current == "desc" else "desc") if on else SORT_FIRST.get(key, "asc"))
        heads[key] = {"aria": ("ascending" if current == "asc" else "descending") if on else None, "sort": sort,
                      "dir": dir, "href": "/transactions?" + urlencode({**filters, "sort": sort, "dir": dir})}
    return heads


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
    if any(r["one_off"] for r in rows):     # the figure averages, projections and the one-offs check use
        line += f" · {overview.money_pence(sum(r['amount'] for r in rows if not r['one_off']), signed=True)} without one-offs"
    return line


def _context(conn, filters: dict) -> dict:
    rows = reports.transactions(conn, month=filters["month"], category_id=_opt_int(filters["category_id"]),
                                q=filters["q"] or None, uncategorised=bool(filters["uncategorised"]),
                                account_id=_opt_int(filters["account_id"]), sort=filters["sort"] or "date",
                                descending=filters["dir"] != "asc")
    categories = reports.category_options(conn)
    filter_categories = reports.category_filter_options(conn)
    accounts = reports.accounts(conn)
    total = sum(r["amount"] for r in rows)
    return {"rows": rows, "total": total, "filters": filters, "categories": categories, "accounts": accounts,
            "filter_categories": filter_categories,
            "months": reports.months_available(conn), "query": urlencode(filters), "suggest": categorise.suggest_pattern,
            "show_accounts": len({a["owner"] for a in accounts}) > 1, "sort_heads": _sort_heads(filters),
            "result_line": _result_line(filters, rows, total, filter_categories, accounts),
            "has_any": bool(conn.execute("SELECT 1 FROM transactions LIMIT 1").fetchone())}


def _page(request: Request, conn, filters: dict, partial: bool) -> HTMLResponse:
    return render(request, "_txn_results.html" if partial else "transactions.html", **_context(conn, filters))


def _default_month(conn) -> str | None:
    """The bare page opens on the month being closed (all of history is ~0.5MB of rows); "All time" stays in the
    Month select, and any link that carries a filter but no month still means all months."""
    return review.latest_open_month(conn) or next(iter(reports.months_available(conn)), None)


def _view(conn, view: str | None) -> dict | None:
    """The filters the page is showing, from the query string htmx pushed to its URL (not the filter form, which may
    hold changes not yet applied). None when the request did not say, so a row is re-rendered as before."""
    if view is None:
        return None
    params = parse_qs(view.lstrip("?"))
    get = lambda key: (params.get(key) or [None])[0]   # noqa: E731
    return _filters(get("month") if params else _default_month(conn), get("category_id"), get("q"),
                    get("uncategorised"), get("account_id"), get("sort"), get("dir"))


def _after_change(request: Request, conn, txn_id: int, filters: dict | None) -> HTMLResponse:
    """The response to a change to one row. With the page's filters known: the row (or nothing, when it no longer
    matches them) plus the result line out of band; the whole results region once the
    last row has gone, so the empty state shows."""
    if filters is None:
        return _row(request, conn, txn_id)
    ctx = _context(conn, filters)
    if not ctx["rows"]:
        resp = render(request, "_txn_results.html", **ctx)
        resp.headers["HX-Retarget"], resp.headers["HX-Reswap"] = f"#{RESULTS}", "outerHTML"
        return resp
    keep = any(r["id"] == txn_id for r in ctx["rows"])
    resp = render(request, "_txn_changed.html", keep=keep, r=reports.transaction(conn, txn_id),
                  suggest=categorise.suggest_pattern, error=None, value=None, result_line=ctx["result_line"])
    if not keep:
        resp.headers["HX-Reswap"] = "delete"
    return resp


def _row(request: Request, conn, txn_id: int, error: str | None = None, value: str | None = None) -> HTMLResponse:
    return render(request, "_txn_row.html", r=reports.transaction(conn, txn_id), suggest=categorise.suggest_pattern,
                  error=error, value=value)


@router.get("/transactions", response_class=HTMLResponse)
def transactions(
    request: Request, conn: Conn,
    month: str | None = None, category_id: str | None = None, q: str | None = None,
    uncategorised: str | None = None, account_id: str | None = None, sort: str | None = None, dir: str | None = None,
):
    if not request.query_params:
        month = _default_month(conn)
    filters = _filters(month, category_id, q, uncategorised, account_id, sort, dir)
    partial = request.headers.get("HX-Target") == RESULTS and not request.headers.get("HX-History-Restore-Request")
    return _page(request, conn, filters, partial)


@router.post("/transactions/{txn_id:int}/category", response_class=HTMLResponse)
def set_transaction_category(
    request: Request, conn: Conn, txn_id: int,
    category: str = Form(""), category_id: str = Form(""), create_rule: str = Form(""), pattern: str = Form(""),
    view: str | None = Form(None),
):
    """The row's combobox posts a label; an id is accepted too. Unknown text re-renders the row with the error.
    `view` is the page's query string: a row that no longer matches it leaves the list (see _after_change)."""
    if category_id:
        cat, error = int(category_id), None
    else:
        cat, error = resolve_category(reports.category_options(conn), category)
    if error:
        return _row(request, conn, txn_id, error=error, value=category)
    before = reports.transaction(conn, txn_id)
    categorise.set_category(conn, txn_id, cat)
    row = reports.transaction(conn, txn_id)
    label = row["category_name"] if row and row["category_id"] else None
    toast = f"Filed under {label}" if label else "Category cleared"
    undo = {"label": "Undo", "post": f"/transactions/{txn_id}/undo", "target": f"#{RESULTS}", "values": {
        "category_id": before["category_id"] or "", "categorised_by": before["categorised_by"] or "",
        "rule_id": before["rule_id"] or ""}} if before else None
    if cat is not None and create_rule and pattern.strip():
        # No Undo once a rule is made: the rule is its own thing, switched off or deleted on Categories and rules.
        categorise.create_rule(conn, pattern=pattern, category_id=cat)
        rule_applied, undo = categorise.apply_rules(conn), None
        toast += f" · rule made, {rule_applied} more filed" if rule_applied else " · rule made"
        if rule_applied:
            # The page reloads to show every row the rule filed; a toast sent now would die with it, so it waits in
            # the flash cookie for the reloaded page.
            resp = flash(_row(request, conn, txn_id), toast)
            resp.headers["HX-Refresh"] = "true"
            return resp
    return hx_events(_after_change(request, conn, txn_id, _view(conn, view)), refresh=True, toast=toast, action=undo)


@router.post("/transactions/{txn_id:int}/undo", response_class=HTMLResponse)
def undo_category(
    request: Request, conn: Conn, txn_id: int,
    category_id: str = Form(""), categorised_by: str = Form(""), rule_id: str = Form(""), view: str | None = Form(None),
):
    """Undo a filing: put the row's previous category back as it was, then re-render the results so the row returns
    to its place under the page's filters."""
    try:
        categorise.restore_category(conn, txn_id, _opt_int(category_id), categorised_by or None, _opt_int(rule_id))
    except ValueError:
        return hx_events(HTMLResponse("", status_code=204), toast="That category no longer exists.", kind="error")
    row = reports.transaction(conn, txn_id)
    toast = f"Back under {row['category_name']}" if row and row["category_id"] else "Back to Uncategorised"
    filters = _view(conn, view)
    if filters is None:
        return hx_events(_row(request, conn, txn_id), refresh=True, toast=toast)
    resp = _page(request, conn, filters, True)
    resp.headers["HX-Retarget"], resp.headers["HX-Reswap"] = f"#{RESULTS}", "outerHTML"
    return hx_events(resp, refresh=True, toast=toast)


@router.post("/transactions/{txn_id:int}/one_off", response_class=HTMLResponse)
def toggle_one_off(request: Request, conn: Conn, txn_id: int, one_off: str = Form(""), view: str | None = Form(None)):
    """The checkbox posts its value only when ticked, so a blank means off. The result line's "without one-offs"
    figure changes with it, so it is re-rendered too when the page's filters are known."""
    on = one_off in ("1", "on", "true")
    reports.set_one_off(conn, txn_id, on)
    return hx_events(_after_change(request, conn, txn_id, _view(conn, view)), refresh=True,
                     toast="Marked one-off" if on else "One-off removed")


# ------------------------------------------------------------------- bulk
def _bulk_filters(month, category_id, q, uncategorised, account_id, sort, dir) -> dict:
    return _filters(month or "all", category_id, q, uncategorised, account_id, sort, dir)


@router.post("/transactions/bulk/category", response_class=HTMLResponse)
def bulk_category(
    request: Request, conn: Conn, ids: Annotated[list[str], Form()] = [], category: str = Form(""),
    month: str = Form(""), category_id: str = Form(""), q: str = Form(""), uncategorised: str = Form(""),
    account_id: str = Form(""), sort: str = Form(""), dir: str = Form(""),
):
    """Apply one category to every selected row, then re-render the results under the current filters."""
    filters = _bulk_filters(month, category_id, q, uncategorised, account_id, sort, dir)
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
    account_id: str = Form(""), sort: str = Form(""), dir: str = Form(""),
):
    filters = _bulk_filters(month, category_id, q, uncategorised, account_id, sort, dir)
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
    uncategorised: str | None = None, account_id: str | None = None, sort: str | None = None, dir: str | None = None,
):
    category_id, account_id = _opt_int(category_id), _opt_int(account_id)
    uncategorised = uncategorised in ("1", "true", "on")
    sort, dir = _sort(sort, dir)
    rows = reports.transactions(conn, month=month or "all", category_id=category_id, q=q,
                                uncategorised=uncategorised, account_id=account_id, sort=sort or "date",
                                descending=dir != "asc")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "account", "type", "description", "amount", "category", "subcategory", "categorised_by", "one_off"])
    for r in rows:
        w.writerow([r["date"], r["account_name"], r["type_code"], r["description"], f"{r['amount']:.2f}",
                    r["parent_name"] or r["category_name"] or "", r["category_name"] if r["parent_name"] else "",
                    r["categorised_by"] or "", "1" if r["one_off"] else ""])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=transactions.csv"})
