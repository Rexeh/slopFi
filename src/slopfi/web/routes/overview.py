"""Overview (the dashboard): the latest open month answered in four figures, then where it went."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ... import overview, reports, review
from ..app import Conn, FieldError, _num, _opt_int, redirect, render, templates

router = APIRouter()

# Page-specific formats (whole pounds in tiles, compact from £100k, signed deltas, pence in payment rows).
templates.env.filters.setdefault("money_tile", overview.money_tile)
templates.env.filters.setdefault("money_delta", overview.money_delta)
templates.env.filters.setdefault("money_pence", overview.money_pence)
templates.env.filters.setdefault("date_label", overview.date_label)
templates.env.filters.setdefault("month_range", overview.month_range_label)


def _choose_month(conn, month: str | None) -> tuple[list[str], str | None]:
    """The selected month: the request's if it has data, else the latest open month, else the newest."""
    months = reports.months_available(conn)
    if month in months:
        return months, month
    return months, review.latest_open_month(conn) or (months[0] if months else None)


def _dashboard(request: Request, conn, month: str | None, account_id: str | None,
               errors: dict | None = None, form: dict | None = None, status: int = 200) -> HTMLResponse:
    account = _opt_int(account_id)
    months, month = _choose_month(conn, month)
    if not months:
        return render(request, "dashboard.html", has_data=False, month=None, months=[], status_code=status)
    accounts = reports.accounts(conn)
    data = overview.page(conn, month, account)
    status_line = overview.status_line(review.checklist(conn))
    return render(
        request, "dashboard.html", status_code=status, has_data=True, month=month, months=months,
        accounts=accounts, account_id=account_id or "", show_accounts=len({a["owner"] for a in accounts}) > 1,
        status=status_line, errors=errors or {}, form=form or {}, **data,
    )


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, conn: Conn, month: str | None = None, account_id: str | None = None):
    return _dashboard(request, conn, month, account_id)


@router.post("/overview/fund-target", response_class=HTMLResponse)
def set_fund_target(request: Request, conn: Conn, target: str = Form(""), month: str = Form(""),
                    account_id: str = Form("")):
    """The house-move fund target, kept in settings. Blank clears it; junk re-renders with the error beside the field."""
    try:
        value = _num(target, "fund_target")
    except FieldError as exc:
        return _dashboard(request, conn, month or None, account_id or None,
                          errors={exc.field: exc.message}, form={exc.field: target}, status=400)
    overview.set_fund_target(conn, value)
    query = f"?month={month}&account_id={account_id}" if month else ""
    return redirect(f"/{query}", "Target saved" if value is not None else "Target removed")
