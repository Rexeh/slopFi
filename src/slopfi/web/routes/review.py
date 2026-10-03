"""Review: the month-close checklist as a page, as the sidebar partial, and closing or reopening a month."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ... import reports, review
from ..app import Conn, month_label, redirect, render

router = APIRouter()


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request, conn: Conn, month: str | None = None):
    months = reports.months_available(conn)
    if month not in months:
        month = None
    data = review.checklist(conn, month)
    return render(request, "review.html", checklist=data, months=months)


@router.get("/review/checklist", response_class=HTMLResponse)
def checklist_partial(request: Request, conn: Conn):
    """The sidebar widget; refreshed by `hx-trigger="refresh from:body"` after any row swap."""
    return render(request, "_checklist.html", checklist=review.checklist(conn), suffix="")


@router.post("/review/close")
def close_month(request: Request, conn: Conn, month: str = Form(...)):
    data = review.checklist(conn, month)
    label = month_label(month, True)
    if data["closed"]:
        return redirect(f"/review?month={month}", f"{label} is already closed", kind="info")
    if not data["can_close"]:
        left = data["left"]
        return redirect(f"/review?month={month}",
                        f"{label} is not ready to close: {left} step{'s' if left != 1 else ''} left", kind="error")
    review.close_month(conn, month)
    return redirect(f"/review?month={month}", f"{label} closed")


@router.post("/review/reopen")
def reopen_month(request: Request, conn: Conn, month: str = Form(...)):
    review.reopen_month(conn, month)
    return redirect(f"/review?month={month}", f"{month_label(month, True)} reopened")
