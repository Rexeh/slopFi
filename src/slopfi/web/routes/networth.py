"""Net worth: accounts, assets and liabilities, balance snapshots."""
from __future__ import annotations

from datetime import date as _date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ... import household, reports, spending_views
from ..app import Conn, FieldError, _num, redirect, render

router = APIRouter()
HOLDING_FIELDS = ("name", "kind", "owner", "provider", "value", "valued_at", "rate", "monthly_payment", "fix_end",
                  "term_end", "notes", "mortgage_balance", "mortgage_provider")


def _networth_page(request: Request, conn, edit: int | None = None, errors=None, form=None, status=200,
                   updating: bool = False):
    nw = spending_views.networth_view(conn)
    editing = next((h for h in nw["holdings"] if h["id"] == edit), None) if edit else None
    mortgage = None
    if editing and editing["kind"] == "property":
        mortgage = editing.get("mortgage") or (
            # rate/payment mistakenly saved on the property itself: offer them back on the mortgage section
            {"value": None, "rate": editing["rate"], "monthly_payment": editing["monthly_payment"],
             "fix_end": editing["fix_end"], "term_end": editing["term_end"], "provider": None}
            if editing["rate"] or editing["monthly_payment"] else None)
    kinds = {k: v for k, v in reports.KIND_LABELS.items() if k != "mortgage"}   # a mortgage is entered on its property
    errors = errors or {}
    dialog_open = bool(editing) or any(k in HOLDING_FIELDS for k in errors) or edit == 0
    return render(request, "networth.html", status_code=status, nw=nw, kinds=kinds, editing=editing, mortgage=mortgage,
                  today=_date.today().isoformat(), owners=household.owner_choices(conn), errors=errors, form=form or {}, dialog_open=dialog_open,
                  updating=updating or any(k.startswith("balance_") for k in errors), fmt=spending_views,
                  stale_days=spending_views.review.STALE_DAYS)


@router.get("/networth", response_class=HTMLResponse)
def networth(request: Request, conn: Conn, edit: int | None = None, update: str | None = None):
    """`?edit=<id>` opens the dialog on that asset (deep link and no-JS fallback; the Edit buttons prefill it in
    the page); `?update=1` opens the balance fields."""
    return _networth_page(request, conn, edit, updating=update == "1")


@router.post("/holdings")
def save_holding(
    request: Request, conn: Conn, name: str = Form(...), kind: str = Form(...), owner: str = Form("joint"),
    provider: str = Form(""), value: str = Form(...), valued_at: str = Form(...), rate: str = Form(""),
    monthly_payment: str = Form(""), fix_end: str = Form(""), term_end: str = Form(""), notes: str = Form(""),
    holding_id: str = Form(""), mortgage_balance: str = Form(""), mortgage_provider: str = Form(""),
):
    hid = int(holding_id) if holding_id else None
    try:
        data = {
            "name": name.strip(), "kind": kind, "owner": owner, "provider": provider.strip() or None,
            "value": abs(_num(value, "value") or 0.0), "valued_at": valued_at, "rate": _num(rate, "rate"),
            "monthly_payment": _num(monthly_payment, "monthly_payment"),
            "fix_end": fix_end or None, "term_end": term_end or None, "notes": notes.strip() or None,
        }
        mortgage_value = abs(_num(mortgage_balance, "mortgage_balance") or 0.0)
    except FieldError as exc:
        return _networth_page(request, conn, hid or 0, errors={exc.field: exc.message}, status=400,
                              form={"name": name, "kind": kind, "owner": owner, "provider": provider, "value": value,
                                    "valued_at": valued_at, "rate": rate, "monthly_payment": monthly_payment,
                                    "fix_end": fix_end, "term_end": term_end, "notes": notes, "holding_id": holding_id,
                                    "mortgage_balance": mortgage_balance, "mortgage_provider": mortgage_provider})
    if kind == "property":
        mortgage = {"value": mortgage_value, "provider": mortgage_provider.strip() or None,
                    "rate": data["rate"], "monthly_payment": data["monthly_payment"],
                    "fix_end": fix_end or None, "term_end": term_end or None}
        reports.save_property(conn, data, mortgage, hid)
    else:
        reports.save_holding(conn, data, hid)
    return redirect("/networth", f"{data['name']} saved")


@router.post("/holdings/{holding_id}/delete")
def delete_holding(conn: Conn, holding_id: int):
    row = conn.execute("SELECT name FROM holdings WHERE id = ?", (holding_id,)).fetchone()
    reports.delete_holding(conn, holding_id)
    return redirect("/networth", f"{row['name']} deleted" if row else "Deleted")


@router.post("/accounts/{account_id}/balance")
def add_balance(request: Request, conn: Conn, account_id: int, date: str = Form(...), balance: str = Form(...)):
    try:
        value = _num(balance, f"balance_{account_id}") or 0.0
    except FieldError as exc:
        return _networth_page(request, conn, errors={exc.field: exc.message}, form={exc.field: balance}, status=400)
    reports.add_balance_snapshot(conn, account_id, date, value)
    return redirect("/networth", "Balance saved")


@router.post("/networth/balances")
async def save_balances(request: Request, conn: Conn):
    """Update balances: one form, a date and amount per account; rows left blank are skipped."""
    posted = await request.form()
    saved, errors, form = [], {}, {}
    for a in reports.account_balances(conn):
        raw = (posted.get(f"balance_{a['id']}") or "").strip()
        if not raw:
            continue
        form[f"balance_{a['id']}"] = raw
        try:
            value = _num(raw, f"balance_{a['id']}") or 0.0
        except FieldError as exc:
            errors[exc.field] = exc.message
            continue
        saved.append((a["id"], posted.get(f"date_{a['id']}") or _date.today().isoformat(), value, a["name"]))
    if errors:
        return _networth_page(request, conn, errors=errors, form=form, status=400, updating=True)
    for account_id, day, value, _ in saved:
        reports.add_balance_snapshot(conn, account_id, day, value)
    n = len(saved)
    message = "No balances entered" if n == 0 else (f"{saved[0][3]} balance saved" if n == 1 else f"{n} balances saved")
    return redirect("/networth", message, "info" if n == 0 else "success")
