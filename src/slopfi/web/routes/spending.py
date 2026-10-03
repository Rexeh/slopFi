"""Spending: categories, fixed costs, targets and savings ideas on one page (/spending); /trends and /recurring
are the old names."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import reports, spending_views
from ..app import Conn, FieldError, _num, _opt_int, redirect, render

router = APIRouter()
TABS = ("categories", "fixed", "targets", "ideas")


def _flag(value: str | None, applied: bool, default: bool) -> bool:
    """A checkbox is absent from the query when unticked, so once the form has been applied, absent means off."""
    if value is None:
        return default and not applied
    return value == "1"


def _spending_page(request: Request, conn, months: int = 3, account_id: str | None = None,
                   include_partial: str | None = None, include_one_off: str | None = None, errors=None, form=None,
                   status=200, tab: str = "categories"):
    account = _opt_int(account_id)
    months = months if months in (3, 6, 12) else 3
    applied = "months" in request.query_params
    partial = _flag(include_partial, applied, True)
    one_off = _flag(include_one_off, applied, False)
    chosen, cov = spending_views.period_months(conn, months, include_partial=partial)
    view = spending_views.spending_view(conn, chosen, cov, account, exclude_one_off=not one_off) if chosen else None
    one_off_count = conn.execute("SELECT COUNT(*) FROM transactions WHERE one_off = 1").fetchone()[0]
    query = request.url.query
    return render(
        request, "spending.html", status_code=status, months=months, chosen=chosen, coverage=cov, view=view,
        accounts=reports.accounts(conn), account_id=account_id or "", include_partial=partial, include_one_off=one_off,
        one_off_count=one_off_count, errors=errors or {}, form=form or {}, tab=tab if tab in TABS else "categories",
        next_url=f"/spending?{query}" if query else "/spending", fmt=spending_views,
    )


@router.get("/spending", response_class=HTMLResponse)
def spending(request: Request, conn: Conn, months: int = 3, account_id: str | None = None,
             include_partial: str | None = None, include_one_off: str | None = None):
    return _spending_page(request, conn, months, account_id, include_partial, include_one_off)


@router.get("/trends")
def trends_redirect(request: Request):
    """Trends became Spending; keep old links and bookmarks working."""
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/spending{query}", status_code=308)


@router.get("/recurring")
def recurring_redirect(request: Request):
    """Recurring payments became the Fixed costs tab of Spending; the fragment opens that tab."""
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/spending{query}#fixed", status_code=308)


@router.post("/budgets/{category_id}")
def set_budget(request: Request, conn: Conn, category_id: int, target: str = Form(""), next: str = Form("/spending")):
    next = next if next.startswith("/") else "/spending"
    try:
        value = _num(target, f"target_{category_id}")
    except FieldError as exc:
        # Re-render the page the form came from, on the Targets tab, with the error beside its field.
        q = {k: v[0] for k, v in parse_qs(urlsplit(next).query).items()}
        return _spending_page(request, conn, int(q.get("months", 3) or 3), q.get("account_id"),
                              q.get("include_partial"), q.get("include_one_off"),
                              errors={exc.field: exc.message}, form={exc.field: target}, status=400, tab="targets")
    reports.set_budget(conn, category_id, value)
    return redirect(next, "Target saved" if value is not None else "Target removed")   # the form's next carries #targets
