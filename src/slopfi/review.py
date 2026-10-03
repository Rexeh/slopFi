"""Month-close review: the five-step checklist for the latest open month, and closed months."""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date

from . import reports
from .db import now_iso

STALE_DAYS = 30          # a balance older than this needs updating
SPIKE_FACTOR = 2.0       # a category more than this many times its 3-month average is a possible one-off
STEPS = 5


# ------------------------------------------------------------- closed months
def closed_months(conn: sqlite3.Connection) -> list[str]:
    return [r["month"] for r in conn.execute("SELECT month FROM closed_months ORDER BY month DESC")]


def close_month(conn: sqlite3.Connection, month: str) -> None:
    with conn:
        conn.execute("INSERT OR IGNORE INTO closed_months (month, closed_at) VALUES (?, ?)", (month, now_iso()))


def reopen_month(conn: sqlite3.Connection, month: str) -> None:
    with conn:
        conn.execute("DELETE FROM closed_months WHERE month = ?", (month,))


def latest_open_month(conn: sqlite3.Connection, today: date | None = None) -> str | None:
    """Newest month with data that is not closed and is not the current (still running) calendar month.
    If only the current month has data, that is the open month."""
    today = today or date.today()
    months = reports.months_available(conn)          # newest first
    if not months:
        return None
    closed = set(closed_months(conn))
    current = today.strftime("%Y-%m")
    for m in months:
        if m not in closed and m < current:
            return m
    return next((m for m in months if m not in closed), None)


# ----------------------------------------------------------------- checklist
def date_label(iso: str | None) -> str:
    """'2026-09-27' -> '27 Sep 2026'."""
    if not iso:
        return "never"
    d = date.fromisoformat(iso)
    return f"{d.day} {calendar.month_abbr[d.month]} {d.year}"


def _day(d: date) -> str:
    return f"{d.day} {calendar.month_abbr[d.month]}"


def coverage_gaps(conn: sqlite3.Connection, month: str) -> list[str]:
    """One sentence fragment per spending account that does not cover the whole month:
    'Amex Alex ends 5 Sep', 'Monzo Sam starts 3 Sep', 'HSBC Joint only 3–20 Sep', 'Monzo Joint missing'."""
    y, mo = int(month[:4]), int(month[5:7])
    start, end = date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1])
    out = []
    for a in reports.coverage(conn):
        if a["kind"] == "savings":
            continue
        first, last = date.fromisoformat(a["first"]), date.fromisoformat(a["last"])
        if first > end or last < start:
            out.append(f"{a['name']} missing")
        elif first > start and last < end:
            out.append(f"{a['name']} only {first.day}–{_day(last)}")
        elif first > start:
            out.append(f"{a['name']} starts {_day(first)}")
        elif last < end:
            out.append(f"{a['name']} ends {_day(last)}")
    return out


def _spikes(conn: sqlite3.Connection, month: str) -> list[dict]:
    """Top-level categories (and income) whose month, with one-offs removed, is over SPIKE_FACTOR × the
    average of the previous three months. Marking the odd transaction one-off clears the spike."""
    earlier = [m for m in reports.months_available(conn) if m < month][:3]
    if not earlier:
        return []
    months = earlier + [month]
    placeholders = ",".join("?" * len(months))
    rows = conn.execute(
        f"""SELECT COALESCE(p.id, c.id) AS top_id, COALESCE(p.name, c.name) AS top_name, c.kind,
                   substr(t.date, 1, 7) AS month, SUM(t.amount) AS total
            FROM transactions t {reports.CATEGORY_JOIN}
            WHERE t.one_off = 0 AND t.category_id IS NOT NULL AND c.kind IN ('expense', 'income')
              AND substr(t.date, 1, 7) IN ({placeholders})
            GROUP BY top_id, month""",
        months,
    ).fetchall()
    by_cat: dict[int, dict] = {}
    for r in rows:
        c = by_cat.setdefault(r["top_id"], {"id": r["top_id"], "name": r["top_name"], "kind": r["kind"], "values": {}})
        c["values"][r["month"]] = abs(r["total"])
    out = []
    for c in by_cat.values():
        avg = sum(c["values"].get(m, 0.0) for m in earlier) / len(earlier)
        this = c["values"].get(month, 0.0)
        if avg > 0 and this > SPIKE_FACTOR * avg:
            out.append({"id": c["id"], "name": c["name"], "kind": c["kind"], "amount": this, "typical": avg,
                        "ratio": this / avg})
    out.sort(key=lambda s: -s["ratio"])
    return out


def _stale_accounts(conn: sqlite3.Connection, today: date) -> list[dict]:
    out = []
    for a in reports.account_balances(conn):
        age = (today - date.fromisoformat(a["date"])).days if a["date"] else None
        if age is None or age > STALE_DAYS:
            out.append({**a, "age": age})
    return out


def _over_target(conn: sqlite3.Connection, month: str) -> list[dict]:
    budgets = reports.budgets(conn)
    if not budgets:
        return []
    out = []
    for b in reports.category_breakdown(conn, month):
        t = budgets.get(b["id"])
        if t and b["spend"] > t["monthly_target"]:
            out.append({"id": b["id"], "name": b["name"], "spend": b["spend"], "target": t["monthly_target"],
                        "over": b["spend"] - t["monthly_target"]})
    return out


def checklist(conn: sqlite3.Connection, month: str | None = None, today: date | None = None) -> dict:
    """The five month-close steps for `month` (default: the latest open month)."""
    today = today or date.today()
    closed = closed_months(conn)
    month = month or latest_open_month(conn, today) or (closed[0] if closed else None)   # all closed: show the newest
    uncategorised_total = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_id IS NULL").fetchone()[0]
    if month is None:
        return {"month": None, "steps": [], "done": 0, "total": STEPS, "left": 0, "closed": False,
                "can_close": False, "closed_months": closed, "uncategorised_total": uncategorised_total}

    n_accounts = len([a for a in reports.coverage(conn) if a["kind"] != "savings"])
    gaps = coverage_gaps(conn, month)
    summary = next((m for m in reports.monthly_summary(conn) if m["month"] == month), None)
    uncat_n = summary["uncategorised_count"] if summary else 0
    uncat_amount = summary["uncategorised_amount"] if summary else 0.0
    spikes = _spikes(conn, month)
    stale = _stale_accounts(conn, today)
    over = _over_target(conn, month)
    has_targets = bool(reports.budgets(conn))

    steps = [
        {"key": "statements", "title": "Statements imported", "done": not gaps,
         "count": f"{n_accounts - len(gaps)} of {n_accounts}", "href": "/statements", "action": "Import",
         "detail": "; ".join(gaps) if gaps else "Every account covers the month."},
        {"key": "categorise", "title": "Categorise", "done": uncat_n == 0,
         "count": f"{uncat_n} left" if uncat_n else "none left",
         "href": f"/transactions?month={month}&uncategorised=1", "action": f"File them ({uncat_n})" if uncat_n else "Open",
         "detail": f"{uncat_n} transactions still to categorise ({reports.money_whole(uncat_amount)})." if uncat_n
                   else "Everything is filed.", "amount": uncat_amount},
        {"key": "one_offs", "title": "One-offs checked", "done": not spikes,
         "count": f"{len(spikes)} to check" if spikes else "0",
         "href": f"/transactions?month={month}" + (f"&category_id={spikes[0]['id']}" if spikes else ""), "action": "Check",
         "detail": "; ".join(f"{s['name']} {reports.money_whole(s['amount'])} is {s['ratio']:.1f}× typical" for s in spikes)
                   if spikes else "No category is far above its usual level.", "spikes": spikes},
        {"key": "balances", "title": "Update balances", "done": not stale,
         "count": f"{len(stale)} stale" if stale else "0", "href": "/networth",
         "action": f"Update ({len(stale)} stale)" if stale else "Open",
         "detail": "; ".join(f"{a['name']} " + (f"last updated {date_label(a['date'])}" if a["date"] else "never updated")
                             for a in stale)
                   if stale else f"Every balance is under {STALE_DAYS} days old.", "stale": stale},
        {"key": "targets", "title": "Targets checked", "done": not over,
         "count": (f"{len(over)} over" if over else "none over") if has_targets else "none set",
         "href": "/spending", "action": "Set targets" if not has_targets else "Review",
         "detail": "; ".join(f"{o['name']} {reports.money_whole(o['over'])} over" for o in over) if over
                   else ("No category is over its target." if has_targets else "No targets set yet."), "over": over},
    ]
    done = sum(1 for s in steps if s["done"])
    is_closed = month in closed
    return {"month": month, "steps": steps, "done": done, "total": STEPS, "left": 0 if is_closed else STEPS - done,
            "closed": is_closed, "can_close": done == STEPS and not is_closed, "closed_months": closed,
            "uncategorised_total": uncategorised_total}
