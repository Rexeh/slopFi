"""FastAPI web UI: the app object, template environment and shared helpers. Routes live in routes/."""
from __future__ import annotations

import json
import sqlite3
from functools import cached_property
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, unquote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from .. import db, household, reports, review

BASE = Path(__file__).parent
app = FastAPI(title="slopFi")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]
NUMBER_MESSAGE = "Enter a number, like 1250 or 3.5."


# ------------------------------------------------------------ template helpers
def money(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "–"
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}£{abs(value):,.2f}"


def month_label(month: str | None, long: bool = False) -> str:
    if not month or month == "all":
        return "All time"
    y, m = month.split("-")
    return f"{(MONTHS_LONG if long else MONTHS)[int(m) - 1]} {y}"


def month_name(month: str | None) -> str:
    """'2026-09' -> 'September' (the checklist's "Close September")."""
    return MONTHS_LONG[int(month[5:7]) - 1] if month and month != "all" else "the month"


def sparkline(values: list[float], width: int = 96, height: int = 24) -> str:
    """SVG polyline points for a tiny inline chart (0 at the bottom, max at the top)."""
    if not values:
        return ""
    top = max(values) or 1.0
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = 2 + (width - 4) * (i / (n - 1) if n > 1 else 0.5)
        y = height - 2 - (height - 4) * (v / top)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def pct(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "–"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.0f}%"


def static_url(path: str) -> str:
    """/static/x.js?v=<mtime> so a changed file is never served from the browser cache."""
    f = BASE / "static" / path.lstrip("/")
    try:
        return f"/static/{path.lstrip('/')}?v={int(f.stat().st_mtime)}"
    except OSError:
        return f"/static/{path.lstrip('/')}"


def icon(name: str, small: bool = False, title: str | None = None) -> Markup:
    """<svg><use> into the inline sprite from _icons.html. An icon that is the only label gets a title."""
    cls = "icon icon-sm" if small else "icon"
    label = f"<title>{title}</title>" if title else ""
    aria = f'role="img" aria-label="{title}"' if title else 'aria-hidden="true"'
    return Markup(f'<svg class="{cls}" viewBox="0 0 20 20" {aria}>{label}<use href="#i-{name}"/></svg>')


templates.env.globals["static_url"] = static_url
templates.env.globals["sparkline"] = sparkline
templates.env.globals["icon"] = icon
templates.env.filters["money"] = money
templates.env.filters["money_whole"] = reports.money_whole
templates.env.filters["month_label"] = month_label
templates.env.filters["month_name"] = month_name
templates.env.filters["pct"] = pct


# -------------------------------------------------------------- connection
def get_conn(request: Request):
    conn = db.connect()
    request.state.conn = conn
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


# ----------------------------------------------------------- form parsing
class FieldError(ValueError):
    """A form field that could not be parsed; rendered beside the field, never as a 500."""

    def __init__(self, field: str, message: str = NUMBER_MESSAGE):
        super().__init__(message)
        self.field, self.message = field, message


def _opt_int(value: str | None) -> int | None:
    """Query parameters from <select> elements arrive as '' when nothing is chosen."""
    return int(value) if value not in (None, "") else None


def _num(value: str | None, field: str = "value") -> float | None:
    """Parse a money or percentage field ('£1,250', '3.5%', '−200'); blank is None; junk raises FieldError."""
    if value is None or not value.strip():
        return None
    cleaned = value.replace("£", "").replace(",", "").replace("%", "").replace("−", "-").strip()
    try:
        return float(cleaned)
    except ValueError:
        raise FieldError(field) from None


def _int(value: str | None, field: str) -> int | None:
    n = _num(value, field)
    if n is None:
        return None
    if n != int(n):
        raise FieldError(field, "Enter a whole number.")
    return int(n)


# ------------------------------------------------------------- rendering
class Shell:
    """Sidebar data, computed only when a template asks for it (htmx fragments never do)."""

    def __init__(self, conn: sqlite3.Connection | None, path: str):
        self.conn, self.path = conn, path

    @cached_property
    def checklist(self) -> dict:
        if self.conn is None:
            return {"month": None, "steps": [], "done": 0, "total": 5, "left": 0, "closed": False, "can_close": False,
                    "closed_months": [], "uncategorised_total": 0}
        return review.checklist(self.conn)

    @cached_property
    def household_subtitle(self) -> str:
        """'Household · Alex and Sam' under the brand, or the household's name when it has no people yet."""
        return household.subtitle(self.conn) if self.conn is not None else household.DEFAULT_NAME

    @property
    def uncategorised(self) -> int:
        return self.checklist["uncategorised_total"]

    @property
    def review_left(self) -> int:
        return self.checklist["left"]


def render(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
    ctx.setdefault("request", request)
    ctx.setdefault("path", request.url.path)
    ctx.setdefault("errors", {})
    ctx.setdefault("shell", Shell(getattr(request.state, "conn", None), request.url.path))
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def flash(response: Response, message: str, kind: str = "success") -> Response:
    """Queue a toast for the next page load: a short-lived cookie app.js consumes, never the query string."""
    response.set_cookie("flash", quote(json.dumps({"message": message, "kind": kind})), max_age=60, path="/",
                        samesite="lax")
    return response


def read_flash(request: Request) -> dict | None:
    raw = request.cookies.get("flash")
    try:
        return json.loads(unquote(raw)) if raw else None
    except ValueError:
        return None


def redirect(url: str, message: str | None = None, kind: str = "success") -> RedirectResponse:
    resp = RedirectResponse(url, status_code=303)
    return flash(resp, message, kind) if message else resp


def hx_events(response: Response, refresh: bool = False, toast: str | None = None, kind: str = "success",
              action: dict | None = None) -> Response:
    """HX-Trigger header: `refresh` re-fetches the sidebar checklist; `toast` is announced by app.js, with an optional
    `action`: {label, href} for a link, or {label, post, values, target} for a button that posts (Undo)."""
    events: dict = {}
    if refresh:
        events["refresh"] = True
    if toast:
        events["toast"] = {"message": toast, "kind": kind, **({"action": action} if action else {})}
    if events:
        response.headers["HX-Trigger"] = json.dumps(events)
    return response


def _select_month(conn: sqlite3.Connection, month: str | None) -> tuple[list[str], str]:
    months = reports.months_available(conn)
    if month is None or (month != "all" and month not in months):
        month = months[0] if months else "all"
    return months, month


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Browsers that ignore the <link rel=icon> still get the logo mark."""
    return RedirectResponse("/static/logo-mark.png", status_code=308)


@app.get("/health")
def health() -> Response:
    return Response("ok", media_type="text/plain")


# Routers import the helpers above, so they are included last. Always import the app through this module.
from .routes import household as household_routes, networth, overview, projection, review as review_routes, setup, spending, transactions  # noqa: E402,I001

for _mod in (overview, transactions, spending, networth, projection, household_routes, setup, review_routes):
    app.include_router(_mod.router)
