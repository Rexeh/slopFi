"""Overview page queries: the "typical" window, spending against typical, six months with a rolling average,
largest payments, net worth since the last snapshot, the house-move fund target and the status line.

"Typical" has one definition everywhere on the page: the average of the three months with data immediately
before the selected month (Jun–Aug for September). Spending uses the same rule as monthly_summary's outgoings
(expense categories, refunds netted off, plus uncategorised debits) so the tile equals the table to the pound.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from . import db, reports, review

TYPICAL_N = 3            # months in the typical window
ABOUT_TYPICAL = 50.0     # a difference under this is "about typical"
ROLLING_N = 3            # months in the six-months chart's rolling average
MIN_ROLLING = 2          # full months needed before a rolling-average point is drawn
FUND_KEY = "fund.target"  # settings key for the house-move fund target

MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]

SPENDING_RULE = "(c.kind = 'expense' OR (t.category_id IS NULL AND t.amount < 0))"


# ------------------------------------------------------------- formatting
def money_tile(value: float | None) -> str:
    """Tile figures: whole pounds to £99,999, then one decimal thousand (£280.4k). True minus."""
    if value is None:
        return "—"
    sign = "−" if value < 0 else ""
    v = abs(value)
    if v >= 100_000:
        return f"{sign}£{v / 1000:,.1f}k".replace(".0k", "k")
    return f"{sign}£{v:,.0f}"


def money_delta(value: float | None) -> str:
    """Signed whole pounds with an explicit + and a true minus: the delta slot."""
    if value is None:
        return "—"
    if abs(value) < 0.5:
        return "£0"
    return ("+" if value > 0 else "−") + f"£{abs(value):,.0f}"


def money_pence(value: float | None, signed: bool = False) -> str:
    """Transaction rows: pence, true minus, optional explicit plus."""
    if value is None:
        return "—"
    sign = "−" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}£{abs(value):,.2f}"


def date_label(iso: str | None) -> str:
    """'2026-09-27' -> '27 Sep 2026'."""
    if not iso:
        return ""
    y, m, d = iso[:10].split("-")
    return f"{int(d)} {MONTHS_SHORT[int(m) - 1]} {y}"


def month_range_label(months: list[str], long: bool = False) -> str:
    """['2026-06','2026-07','2026-08'] -> 'Jun–Aug'; across years 'Nov 2025–Jan 2026'; one month 'Aug'."""
    if not months:
        return ""
    names = MONTHS_LONG if long else MONTHS_SHORT
    first, last = sorted(months)[0], sorted(months)[-1]
    if first == last:
        return names[int(first[5:7]) - 1]
    if first[:4] == last[:4]:
        return f"{names[int(first[5:7]) - 1]}–{names[int(last[5:7]) - 1]}"
    return f"{names[int(first[5:7]) - 1]} {first[:4]}–{names[int(last[5:7]) - 1]} {last[:4]}"


# ---------------------------------------------------------------- typical
def typical_months(conn: sqlite3.Connection, month: str) -> list[str]:
    """The three months with data immediately before `month`, oldest first (fewer if the history is short)."""
    return sorted(m for m in reports.months_available(conn) if m < month)[-TYPICAL_N:]


def spending_by_category(conn: sqlite3.Connection, months: list[str], account_id: int | None = None) -> dict:
    """Spending per top-level category summed over `months`: {top_id|None: {id, name, spend, n}}."""
    if not months:
        return {}
    aclause, aparams = reports._account_filter(account_id)
    rows = conn.execute(
        f"""
        SELECT COALESCE(p.id, c.id) AS top_id, COALESCE(p.name, c.name, 'Uncategorised') AS name,
               SUM(-t.amount) AS spend, COUNT(*) AS n
        FROM transactions t {reports.CATEGORY_JOIN}
        WHERE {SPENDING_RULE} AND substr(t.date, 1, 7) IN ({",".join("?" * len(months))}) {aclause}
        GROUP BY top_id
        """,
        [*months, *aparams],
    ).fetchall()
    return {r["top_id"]: {"id": r["top_id"], "name": r["name"], "spend": r["spend"], "n": r["n"]} for r in rows}


def against_typical(conn: sqlite3.Connection, month: str, account_id: int | None = None) -> dict:
    """Rows for the "Against typical" table and the "Where <month> went" chart, plus totals."""
    typ_months = typical_months(conn, month)
    this = spending_by_category(conn, [month], account_id)
    typ = spending_by_category(conn, typ_months, account_id)
    n = len(typ_months)
    rows = []
    for key in set(this) | set(typ):
        spend = this[key]["spend"] if key in this else 0.0
        typical = (typ[key]["spend"] / n) if key in typ else None
        if abs(spend) < 0.5 and (typical is None or abs(typical) < 0.5):
            continue
        diff = (spend - typical) if typical is not None else None
        state = None if diff is None else ("about" if abs(diff) < ABOUT_TYPICAL else ("up" if diff > 0 else "down"))
        name = this[key]["name"] if key in this else typ[key]["name"]
        rows.append({"id": key, "name": name, "uncategorised": key is None, "spend": spend,
                     "n": this[key]["n"] if key in this else 0, "typical": typical, "diff": diff,
                     "diff_pct": (diff / typical) if diff is not None and typical else None, "state": state})
    rows.sort(key=lambda r: (-r["spend"], -(r["typical"] or 0.0)))
    total = sum(r["spend"] for r in rows)
    typical_total = (sum(v["spend"] for v in typ.values()) / n) if n else None
    for r in rows:
        r["share"] = (r["spend"] / total) if total > 0 and r["spend"] > 0 else 0.0
    total_diff = (total - typical_total) if typical_total is not None else None
    return {"rows": rows, "total": total, "count": sum(r["n"] for r in rows), "typical_months": typ_months,
            "typical_label": month_range_label(typ_months), "typical_total": typical_total, "total_diff": total_diff,
            "total_state": None if total_diff is None else
            ("about" if abs(total_diff) < ABOUT_TYPICAL else ("up" if total_diff > 0 else "down"))}


def typical_summary(conn: sqlite3.Connection, month: str, account_id: int | None = None) -> dict | None:
    """Average spending, income, net and savings rate over the typical window (ratio of sums for the rate)."""
    typ_months = typical_months(conn, month)
    if not typ_months:
        return None
    rows = [m for m in reports.monthly_summary(conn, account_id=account_id) if m["month"] in typ_months]
    n = len(typ_months)
    spending = sum(m["outgoings"] for m in rows) / n
    income = sum(m["income"] for m in rows) / n
    return {"months": typ_months, "label": month_range_label(typ_months), "spending": spending, "income": income,
            "net": income - spending, "savings_rate": ((income - spending) / income) if income else None}


def savings_rate(summary: dict | None) -> float | None:
    if not summary or not summary["income"]:
        return None
    return summary["net"] / summary["income"]


# ------------------------------------------------------------- six months
def six_months(conn: sqlite3.Connection, account_id: int | None = None, n: int = 6,
               today: date | None = None) -> list[dict]:
    """The last `n` months, oldest first, each flagged partial (an account has no data for it, or the month is
    still running) and carrying a rolling average of spending over the full months among the last three."""
    today = today or date.today()
    current = today.strftime("%Y-%m")
    cov = {c["month"]: c for c in reports.month_coverage(conn)}
    out = []
    for m in reports.monthly_summary(conn, account_id=account_id)[-n:]:
        partial = m["month"] >= current or not cov.get(m["month"], {}).get("covered", True)
        out.append({**m, "partial": partial, "avg3": None})
    for i, row in enumerate(out):
        if row["partial"]:
            continue
        window = [r["outgoings"] for r in out[max(0, i - ROLLING_N + 1):i + 1] if not r["partial"]]
        if len(window) >= MIN_ROLLING:
            row["avg3"] = sum(window) / len(window)
    return out


# ------------------------------------------------------- largest payments
def largest_payments(conn: sqlite3.Connection, month: str, account_id: int | None = None, limit: int = 8) -> list[dict]:
    mclause, mparams = reports._month_filter(month)
    aclause, aparams = reports._account_filter(account_id)
    rows = conn.execute(
        f"""
        SELECT t.id, t.date, t.description, t.amount, t.category_id, a.name AS account,
               COALESCE(p.name || ' / ' || c.name, c.name) AS category
        FROM transactions t {reports.CATEGORY_JOIN} JOIN accounts a ON a.id = t.account_id
        WHERE t.amount < 0 AND {SPENDING_RULE} {mclause} {aclause}
        ORDER BY t.amount ASC, t.date DESC LIMIT ?
        """,
        [*mparams, *aparams, limit],
    ).fetchall()
    return [dict(r) for r in rows]


# -------------------------------------------------------------- net worth
def _holdings_value_at(conn: sqlite3.Connection, day: str) -> float:
    rows = conn.execute(
        """SELECT h.kind, (SELECT s.value FROM holding_snapshots s WHERE s.holding_id = h.id AND s.date <= ?
                           ORDER BY s.date DESC LIMIT 1) AS value
           FROM holdings h""",
        (day,),
    ).fetchall()
    return sum((-r["value"] if r["kind"] in reports.LIABILITY_KINDS else r["value"]) for r in rows if r["value"] is not None)


def net_worth_change(conn: sqlite3.Connection) -> dict:
    """Current net worth with the change in assets and liabilities since the previous snapshot date."""
    nw = reports.net_worth(conn)
    dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM holding_snapshots ORDER BY date")]
    out = {"total": nw["total"], "liquid": nw["liquid"], "date": dates[-1] if dates else None, "change": None,
           "since": None, "has_holdings": bool(nw["holdings"]), "has_balances": any(a["balance"] is not None for a in nw["accounts"])}
    if len(dates) >= 2:
        out["since"] = dates[-2]
        out["change"] = _holdings_value_at(conn, dates[-1]) - _holdings_value_at(conn, dates[-2])
    return out


# ---------------------------------------------------------------- the fund
def fund_target(conn: sqlite3.Connection) -> float | None:
    raw = db.get_setting(conn, FUND_KEY)
    try:
        return float(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def set_fund_target(conn: sqlite3.Connection, value: float | None) -> None:
    db.set_setting(conn, FUND_KEY, "" if value is None else f"{value:.2f}")


# ------------------------------------------------------------ status line
def status_line(checklist: dict, today: date | None = None) -> dict:
    """The sentence under the h1 for the latest open month: state plus at most two facts, each a (strong, rest)
    pair so the template can bold the figure."""
    month = checklist.get("month")
    if not month:
        return {"month": None, "state": "none", "facts": []}
    name = MONTHS_LONG[int(month[5:7]) - 1]
    if checklist["closed"]:
        return {"month": month, "name": name, "state": "closed", "facts": []}
    steps = {s["key"]: s for s in checklist["steps"]}
    facts: list[tuple[str, str]] = []
    cat = steps.get("categorise")
    if cat and not cat["done"]:
        n = cat["count"].split()[0]
        facts.append((f"{n} transaction{'s' if n != '1' else ''}", f" still to categorise ({reports.money_whole(cat['amount'])})"))
    bal = steps.get("balances")
    if bal and not bal["done"]:
        stale = bal.get("stale", [])
        if len(stale) == 1:
            a = stale[0]
            facts.append((f"the {a['name']} balance", f" is {a['age']} days old" if a["age"] is not None else " has never been updated"))
        elif stale:
            facts.append((f"{len(stale)} balances", f" are over {review.STALE_DAYS} days old"))
    st = steps.get("statements")
    if st and not st["done"]:
        facts.append(("statements", " are missing or stop early: " + "; ".join(g["text"] for g in st["gaps"])))
    one = steps.get("one_offs")
    if one and not one["done"] and one.get("spikes"):
        s = one["spikes"][0]
        facts.append((f"{s['name']} {reports.money_whole(s['amount'])}", f" is {s['ratio']:.1f}× typical"))
    tg = steps.get("targets")
    if tg and not tg["done"] and tg.get("over"):
        o = tg["over"][0]
        facts.append((f"{o['name']}", f" is {reports.money_whole(o['over'])} over target"))
    state = "ready" if checklist["can_close"] else "open"
    return {"month": month, "name": name, "state": state, "facts": facts[:2], "left": checklist["left"]}


# ------------------------------------------------------------------- page
def page(conn: sqlite3.Connection, month: str, account_id: int | None = None, today: date | None = None) -> dict:
    """Everything the Overview template needs for one month."""
    today = today or date.today()
    summary = next((m for m in reports.monthly_summary(conn, account_id=account_id) if m["month"] == month), None)
    typical = typical_summary(conn, month, account_id)
    against = against_typical(conn, month, account_id)
    months6 = six_months(conn, account_id, today=today)
    rate = savings_rate(summary)
    typical_rate = typical["savings_rate"] if typical else None
    nw = net_worth_change(conn)
    target = fund_target(conn)
    checklist = review.checklist(conn, month, today=today)
    return {
        "summary": summary, "typical": typical, "against": against, "months6": months6,
        "spending": against["total"], "spending_diff": against["total_diff"],
        "spending_pct": (against["total_diff"] / against["typical_total"]) if against["total_diff"] is not None and against["typical_total"] else None,
        "savings_rate": rate, "typical_rate": typical_rate,
        "rate_diff_pts": ((rate - typical_rate) * 100) if rate is not None and typical_rate is not None else None,
        "net_worth": nw, "fund": {"value": nw["liquid"], "target": target,
                                  "progress": (nw["liquid"] / target) if target else None},
        "largest": largest_payments(conn, month, account_id),
        "checklist": checklist, "todo": [s for s in checklist["steps"] if not s["done"]],
        "partial_months": [m["month"] for m in months6 if m["partial"]],
    }
