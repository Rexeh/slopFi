"""Renewals: the register of contracts and dates, adding and editing them, and recording each renewal."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ... import clock, household, renewals, review
from ...spending_views import day_label
from ..app import Conn, FieldError, _num, redirect, render

router = APIRouter()
GROUPS = (("due", "Due before the next close"), ("soon", "Next six months"), ("later", "Later"))
PERIOD_LABELS = {"month": "a month", "year": "a year", "once": ""}
DAYS_PER_MONTH = 30.44


def _when(days: int | None) -> str:
    """'in 14 days', 'in 4 months', 'ended 3 days ago'."""
    if days is None:
        return ""
    if days < 0:
        return f"ended {-days} day{'s' if days != -1 else ''} ago"
    if days == 0:
        return "ends today"
    if days < 60:
        return f"in {days} day{'s' if days != 1 else ''}"
    months = round(days / 30.4)
    return f"in {months} months" if days < 700 else f"in {round(days / 365.25)} years"


def _page(request: Request, conn, *, renew: int | None = None, edit: int | None = None, add: bool = False,
          payee: str | None = None, errors: dict | None = None, form: dict | None = None, status: int = 200):
    today = clock.today()
    owners = household.owner_choices(conn)
    rows = renewals.register(conn, today)
    for r in rows:
        r["when"] = _when(r["days_left"])
        r["owner_label"] = household.owner_label(owners, r["owner"])
        r["period_label"] = PERIOD_LABELS[r["price_period"]]
    active = [r for r in rows if not r["archived"]]
    suggestions = renewals.suggestions(conn)
    editing = next((r for r in rows if r["id"] == edit), None) if edit else None
    prefill = next((s for s in suggestions if s["payee_key"] == payee), None) if payee else None
    expanded = next((r for r in rows if r["id"] == renew and not r["archived"]), None) if renew else None
    return render(
        request, "renewals.html", status_code=status, sections=[(k, label, [r for r in rows if r["group"] == k]) for k, label in GROUPS],
        archived=[r for r in rows if r["archived"]], rows=rows, due_n=len([r for r in rows if r["group"] == "due"]),
        yearly=sum(renewals.annual(r["price"], r["price_period"]) or 0 for r in active), year=today.year,
        saved=renewals.saved_in_year(conn, today.year), suggestions=suggestions, owners=owners,
        kinds={k: v for k, v in renewals.KIND_LABELS.items() if k != "mortgage"}, periods=PERIOD_LABELS,
        payees=renewals.payee_choices(conn), editing=editing, prefill=prefill,
        form_open=add or bool(editing) or bool(prefill) or (bool(errors) and renew is None),
        archived_history={r["id"]: renewals.history(conn, r["id"]) for r in rows if r["archived"]},
        close_month=review.latest_open_month(conn, today),
        expanded=expanded, history=renewals.history(conn, expanded["id"]) if expanded else [],
        default_end=(date.fromisoformat(expanded["ends_on"]) + timedelta(days=365)).isoformat() if expanded and expanded["ends_on"] else "",
        today=today.isoformat(), errors=errors or {}, form=form or {}, day_label=day_label)


@router.get("/renewals", response_class=HTMLResponse)
def renewals_page(request: Request, conn: Conn, renew: int | None = None, edit: int | None = None, add: str | None = None,
                  payee: str | None = None):
    """`?renew=<id>` opens that row's renewal form, `?edit=<id>` or `?add=1` the details form (`&payee=` prefills it
    from a suggestion)."""
    return _page(request, conn, renew=renew, edit=edit, add=add == "1", payee=payee)


def _iso(value: str, field: str, message: str) -> str:
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        raise FieldError(field, message) from None


def _price(value: str, field: str) -> float | None:
    n = _num(value, field)
    if n is not None and n < 0:
        raise FieldError(field, "Enter 0 or more, or leave it blank.")
    return n


@router.post("/renewals")
@router.post("/renewals/{renewal_id}")
def save_renewal(request: Request, conn: Conn, renewal_id: int | None = None, name: str = Form(""), kind: str = Form("other"),
                 provider: str = Form(""), owner: str = Form("joint"), ends_on: str = Form(""), lead_months: str = Form(""),
                 price: str = Form(""), price_period: str = Form("month"), payee_key: str = Form(""), notes: str = Form("")):
    current = conn.execute("SELECT * FROM renewals WHERE id = ?", (renewal_id,)).fetchone() if renewal_id else None
    if renewal_id and current is None:
        return redirect("/renewals", "That renewal no longer exists", kind="info")
    is_mortgage = bool(current and current["holding_id"])
    raw = {"name": name, "kind": kind, "provider": provider, "owner": owner, "ends_on": ends_on, "lead_months": lead_months,
           "price": price, "price_period": price_period, "payee_key": payee_key, "notes": notes}
    try:
        if not is_mortgage and not name.strip():
            raise FieldError("name", "Enter a name.")
        if not is_mortgage and not ends_on.strip():
            raise FieldError("ends_on", "Enter the date it ends.")
        lead = _num(lead_months, "lead_months")
        if lead is not None and not (1 <= lead <= 24 and lead == int(lead)):
            raise FieldError("lead_months", "Enter a whole number of months from 1 to 24, or leave it blank.")
        data = {"name": name.strip(), "kind": kind if kind in renewals.KINDS and kind != "mortgage" else "other",
                "provider": provider.strip() or None, "owner": owner or "joint",
                "ends_on": None if is_mortgage else _iso(ends_on, "ends_on", "Enter the date it ends."),
                "lead_days": round(lead * DAYS_PER_MONTH) if lead is not None else None, "price": _price(price, "price"),
                "price_period": price_period if price_period in renewals.PERIODS else "month",
                "payee_key": payee_key.strip() or None, "notes": notes.strip() or None}
    except FieldError as exc:
        return _page(request, conn, edit=renewal_id, add=renewal_id is None, errors={exc.field: exc.message},
                     form={**raw, "renewal_id": renewal_id or ""}, status=400)
    rid = renewals.save_renewal(conn, data, renewal_id)
    label = current["name"] if is_mortgage else data["name"]
    return redirect(f"/renewals#r-{rid}", f"{label} saved" if renewal_id else f"{label} added")


@router.post("/renewals/{renewal_id}/delete")
def delete_renewal(conn: Conn, renewal_id: int):
    row = conn.execute("SELECT name FROM renewals WHERE id = ?", (renewal_id,)).fetchone()
    if row is None or not renewals.delete_renewal(conn, renewal_id):
        return redirect("/renewals", "The mortgage fix is kept in step with Net worth; change it there", kind="info")
    return redirect("/renewals", f"{row['name']} deleted")


@router.post("/renewals/{renewal_id}/renew")
def renew(request: Request, conn: Conn, renewal_id: int, outcome: str = Form("stayed"), quote: str = Form(""),
          quote_period: str = Form("year"), other_quote: str = Form(""), other_period: str = Form("year"),
          other_provider: str = Form(""), new_provider: str = Form(""), new_price: str = Form(""),
          new_period: str = Form("year"), new_ends_on: str = Form(""), new_rate: str = Form(""), new_payment: str = Form(""),
          done_on: str = Form("")):
    today = clock.today()
    row = renewals.get(conn, renewal_id, today)
    if row is None or row["archived"]:
        return redirect("/renewals", "That renewal is archived or gone", kind="info")
    raw = {"outcome": outcome, "quote": quote, "quote_period": quote_period, "other_quote": other_quote,
           "other_period": other_period, "other_provider": other_provider, "new_provider": new_provider,
           "new_price": new_price, "new_period": new_period, "new_ends_on": new_ends_on, "new_rate": new_rate,
           "new_payment": new_payment, "done_on": done_on}
    try:
        ends = _iso(new_ends_on, "new_ends_on", "Enter the new end date.")
        if ends <= today.isoformat():
            raise FieldError("new_ends_on", "The new end date must be after today.")
        when = _iso(done_on, "done_on", "Enter the date it was done.") if done_on.strip() else today.isoformat()
        rate = _num(new_rate, "new_rate")
        data = {"outcome": outcome if outcome in renewals.OUTCOMES else "stayed",
                "quote": _price(quote, "quote"), "quote_period": quote_period if quote_period in renewals.PERIODS else "year",
                "other_quote": _price(other_quote, "other_quote"),
                "other_period": other_period if other_period in renewals.PERIODS else "year",
                "other_provider": other_provider.strip() or None,
                "new_provider": new_provider.strip() or row["provider"], "new_price": _price(new_price, "new_price"),
                "new_period": new_period if new_period in renewals.PERIODS else row["price_period"],
                "new_ends_on": ends, "new_rate": rate, "new_payment": _price(new_payment, "new_payment")}
        if row["holding_id"]:   # the mortgage's price is its monthly payment; blank keeps the current one
            data["new_price"], data["new_period"] = data["new_payment"] if data["new_payment"] is not None else row["price"], "month"
        elif data["new_price"] is None:
            raise FieldError("new_price", "Enter the new price." if data["outcome"] != "done" else "Enter the price paid.")
    except FieldError as exc:
        return _page(request, conn, renew=renewal_id, errors={exc.field: exc.message}, form=raw, status=400)
    if data["quote"] is None:
        data["quote_period"] = None
    if data["other_quote"] is None:
        data["other_period"] = None
    renewals.record(conn, renewal_id, data, date.fromisoformat(when))
    message = (f"{row['name']} done; next due {day_label(ends)}" if data["outcome"] == "done"
               else f"{row['name']} renewed until {day_label(ends)}")
    return redirect(f"/renewals#r-{renewal_id}", message)


@router.post("/renewals/{renewal_id}/events/latest/delete")
def undo_latest(conn: Conn, renewal_id: int):
    row = conn.execute("SELECT name FROM renewals WHERE id = ?", (renewal_id,)).fetchone()
    if row is None or not renewals.delete_latest_event(conn, renewal_id):
        return redirect("/renewals", "Nothing to undo", kind="info")
    return redirect(f"/renewals?renew={renewal_id}#r-{renewal_id}", f"Last renewal of {row['name']} undone")


@router.post("/renewals/suggestions/dismiss")
def dismiss(conn: Conn, payee_key: str = Form(...)):
    renewals.dismiss_suggestion(conn, payee_key)
    return redirect("/renewals", f"{payee_key.title()} won't be suggested again", kind="info")
