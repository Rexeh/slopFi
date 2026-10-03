"""Phase 3 page views: shapes report data for spending.html and networth.html.

Everything here is derived from reports.py and review.py; nothing writes. Money helpers return whole pounds with a
true minus, as the design's tiles and tables require.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from . import reports, review

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SLOTS = 6                       # categorical chart slots used by the matrix and the small multiples
MULTIPLE_POINTS = 5             # months plotted in each small multiple
SMALL_CHANGE = 50.0             # changes under this are shown without colour
TYPE_LABELS = {"DD": "Direct debit", "SO": "Standing order"}
LTV_BANDS = (60, 75, 85)        # lender rate bands, percent


# ----------------------------------------------------------------- formatting
def money(value: float | None) -> str:
    return reports.money_whole(value)


def signed(value: float | None) -> str:
    """'+£285', '−£158', '£0'."""
    if value is None:
        return "—"
    if round(value) == 0:
        return "£0"
    return ("+" if value > 0 else "−") + f"£{abs(value):,.0f}"


def compact(value: float | None) -> str:
    """Tile figure: whole pounds to £99,999, then '£280.4k'."""
    if value is None:
        return "—"
    if abs(value) >= 100_000:
        return f"{'−' if value < 0 else ''}£{abs(value) / 1000:,.1f}k".replace(".0k", "k")
    return money(value)


def month_short(month: str) -> str:
    return MONTHS[int(month[5:7]) - 1]


def month_label(month: str) -> str:
    return f"{month_short(month)} {month[:4]}"


def span_label(months: list[str]) -> str:
    """'Jul to Sep 2026', 'Nov 2025 to Jan 2026', 'Sep 2026'."""
    if not months:
        return "—"
    if len(months) == 1:
        return month_label(months[0])
    first, last = months[0], months[-1]
    if first[:4] == last[:4]:
        return f"{month_short(first)} to {month_label(last)}"
    return f"{month_label(first)} to {month_label(last)}"


def dash_label(months: list[str]) -> str:
    """Column-header form: 'May–Jun', 'Sep'."""
    if not months:
        return "—"
    if len(months) == 1:
        return month_short(months[0])
    return f"{month_short(months[0])}–{month_short(months[-1])}"


def day_label(iso: str | None) -> str:
    """'2026-09-27' -> '27 Sep 2026'."""
    if not iso:
        return "—"
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


# ------------------------------------------------------------- period choice
def period_months(conn: sqlite3.Connection, n: int, include_partial: bool) -> tuple[list[str], list[dict]]:
    """The last n usable months. A month counts as usable when every spending account covers at least part of it
    and it is complete, closed on Review (the household has signed it off, gaps and all), or gaps are allowed."""
    cov = reports.month_coverage(conn)
    closed = set(review.closed_months(conn))
    usable = [c for c in cov if c["covered"] and (include_partial or c["complete"] or c["month"] in closed)]
    chosen = [c["month"] for c in usable][-n:]
    return chosen, [{**c, "closed": c["month"] in closed} for c in cov if c["month"] in chosen]


# ---------------------------------------------------------- table decoration
def change_text(change: float | None, prev_avg: float | None) -> dict:
    """'+£285' with '(+184%)' or, when the previous figure is too small for a percentage to mean anything,
    '(from £10)'. Never '+999%'."""
    if change is None or prev_avg is None:
        return {"text": "", "detail": "", "tone": "", "title": ""}
    tone = "" if abs(change) < SMALL_CHANGE else ("neg" if change > 0 else "pos")
    pct = (change / prev_avg) if prev_avg else None
    if prev_avg < SMALL_CHANGE or pct is None or abs(pct) > 3:
        detail = f"(from {money(prev_avg)})"
    elif round(abs(pct) * 100) == 0:
        detail = ""
    else:
        detail = f"({'+' if pct > 0 else '−'}{abs(pct):.0%})"
    return {"text": signed(change), "detail": detail, "tone": tone, "title": f"Previously {money(prev_avg)} a month"}


def spark(values: list[float], months: list[str], width: int = 96, height: int = 24) -> dict:
    """Sparkline geometry: muted polyline, end dot, a flat series centred, and a £ label for the row."""
    if not values:
        return {"points": "", "end": None, "label": "No data"}
    lo, hi = min(values), max(values)
    span = hi - lo
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = 2 + (width - 4) * (i / (n - 1) if n > 1 else 0.5)
        y = (height / 2) if span == 0 else (height - 2) - (height - 4) * ((v - lo) / span)
        pts.append((round(x, 1), round(y, 1)))
    if span == 0:
        label = f"{money(values[0])} each month"
    else:
        label = ", ".join(f"{month_short(m)} {money(v)}" for m, v in zip(months, values))
    return {"points": " ".join(f"{x},{y}" for x, y in pts), "end": pts[-1], "label": label}


def _uncategorised_by_month(conn: sqlite3.Connection, months: list[str], account_id, exclude_one_off) -> dict:
    """Money out with no category, per month: the part of monthly_summary()'s outgoings that trend_table() leaves out."""
    if not months:
        return {}
    aclause, aparams = reports._account_filter(account_id)
    one_off = "AND t.one_off = 0" if exclude_one_off else ""
    rows = conn.execute(
        f"""SELECT substr(t.date, 1, 7) AS month, SUM(-t.amount) AS spend, COUNT(*) AS n FROM transactions t
            WHERE t.category_id IS NULL AND t.amount < 0 AND substr(t.date, 1, 7) IN ({",".join("?" * len(months))})
                  {aclause} {one_off} GROUP BY month""", [*months, *aparams]).fetchall()
    return {r["month"]: r["spend"] for r in rows}


def add_uncategorised(conn: sqlite3.Connection, table: dict, account_id, exclude_one_off) -> dict:
    """Append an Uncategorised row so the table's total equals Average spending to the pound, and work every share
    out of that total."""
    months, prev = table["months"], table["prev_months"]
    cur = _uncategorised_by_month(conn, months, account_id, exclude_one_off)
    values = [round(cur.get(m, 0.0), 2) for m in months]
    n = len(months) or 1
    if any(values):
        before = _uncategorised_by_month(conn, prev, account_id, exclude_one_off)
        avg = sum(values) / n
        prev_avg = (sum(before.values()) / len(prev)) if prev else None
        change = (avg - prev_avg) if prev_avg is not None else None
        table["categories"].append({
            "id": None, "name": "Uncategorised", "series": values, "avg": avg, "min": min(values), "max": max(values),
            "last": values[-1], "total": sum(values), "share": 0.0, "fixed_share": 0.0, "prev_avg": prev_avg,
            "change": change, "change_pct": (change / prev_avg) if change is not None and prev_avg else None,
            "lumpy": False, "discretionary": False, "target": None, "gap": None, "children": [], "n": 0})
    total = sum(c["avg"] for c in table["categories"])
    table["totals"]["table_avg"] = total
    table["totals"]["table_last"] = sum(c["last"] for c in table["categories"])
    for c in table["categories"]:
        c["share"] = (c["avg"] / total) if total else 0.0
    return table


def decorate_table(table: dict) -> dict:
    """Add display fields to trend_table()'s categories (in place) and return it."""
    months = table["months"]
    total = table["totals"].get("table_avg", table["totals"]["trend_avg_outgoings"])
    top_share = max([c["share"] for c in table["categories"]] + [0.0])
    for c in table["categories"]:
        c["share_bar"] = round(100 * c["share"] / top_share, 1) if top_share else 0.0
        c["change_view"] = change_text(c["change"], c["prev_avg"])
        c["spark"] = spark(c["series"], months)
        c["is_uncategorised"] = c["id"] is None
        c["badges"] = ([("warning", "Spiky")] if c["lumpy"] else []) + ([("neutral", "Flexible")] if c["discretionary"] else [])
        for s in c["children"]:
            s["spark"] = spark(s["series"], months)
            s["share"] = (s["avg"] / total) if total else 0.0
    return table


# ----------------------------------------------------------- chart data
def nice_ceiling(value: float) -> float:
    """A round axis maximum a little above value: 1,840 -> 2,000; 430 -> 500."""
    import math
    mag = 10 ** math.floor(math.log10(max(value, 1.0)))
    for f in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if f * mag >= value * 1.04:
            return f * mag
    return 10 * mag


def _window_rows(conn, months: list[str], account_id, exclude_one_off) -> tuple[list[str], list]:
    """The MULTIPLE_POINTS months ending at the period's last month, and the category rows for them."""
    if not months:
        return [], []
    all_months = sorted(reports.months_available(conn))
    upto = [m for m in all_months if m <= months[-1]]
    window = upto[-MULTIPLE_POINTS:]
    return window, reports._spend_by_category_month(conn, window, account_id, exclude_one_off)


def multiples(table: dict, window: list[str], rows: list, coverage: list[dict]) -> dict:
    """Six largest categories (by the period's average), five monthly points each, one shared y axis, and the
    previous window's average as the reference rule. The title figure is the table's Average."""
    by_top: dict = {}
    for r in rows:
        by_top.setdefault(r["top_id"], {m: 0.0 for m in window})[r["month"]] += r["spend"]
    prev_label = dash_label(table["prev_months"])
    partial = {c["month"]: not c["complete"] for c in coverage}
    items = []
    for c in table["categories"]:
        if c["id"] is None or len(items) == SLOTS:
            continue
        vals = [round(by_top.get(c["id"], {}).get(m, 0.0), 2) for m in window]
        items.append({"id": c["id"], "name": c["name"], "values": vals, "avg": round(c["avg"]), "prev_avg": c["prev_avg"],
                      "prev_label": f"{prev_label} avg {money(c['prev_avg'])}" if c["prev_avg"] is not None else "",
                      "label": f"{c['name']}: " + ", ".join(f"{month_short(m)} {money(v)}" for m, v in zip(window, vals))
                               + (f"; {prev_label} average {money(c['prev_avg'])}" if c["prev_avg"] is not None else "")})
    ymax = nice_ceiling(max([v for i in items for v in i["values"]] + [i["prev_avg"] or 0 for i in items] + [1.0]))
    return {"months": window, "labels": [month_short(m) + (" (partial)" if partial.get(m) else "") for m in window],
            "ymax": ymax, "charts": items, "period": span_label(table["months"]), "prev": span_label(table["prev_months"])}


def slot_categories(conn: sqlite3.Connection) -> list[dict]:
    """The six top-level categories with the most spending over all time, in a fixed order, so a category keeps its
    colour whatever the period or account filter."""
    rows = conn.execute(
        f"""SELECT COALESCE(p.id, c.id) AS top_id, COALESCE(p.name, c.name) AS top_name, SUM(-t.amount) AS spend
            FROM transactions t {reports.CATEGORY_JOIN}
            WHERE c.kind = 'expense' GROUP BY top_id ORDER BY spend DESC LIMIT ?""", (SLOTS,)).fetchall()
    return [{"id": r["top_id"], "name": r["top_name"], "slot": i + 1} for i, r in enumerate(rows)]


def matrix(conn: sqlite3.Connection, window: list[str], rows: list, coverage: list[dict], uncat: dict) -> dict:
    """Stacked monthly spending: six fixed slots, Other categories, Uncategorised; partial months flagged. Each
    month's stack totals that month's spending."""
    slots = slot_categories(conn)
    slot_ids = {s["id"] for s in slots}
    cell: dict = {"uncat": {m: uncat.get(m, 0.0) for m in window}}
    for r in rows:
        if r["top_id"] is None:
            continue
        key = r["top_id"] if r["top_id"] in slot_ids else "other"
        cell.setdefault(key, {m: 0.0 for m in window})[r["month"]] += r["spend"]
    series = [{"name": s["name"], "token": f"--chart-{s['slot']}", "values": [round(cell.get(s["id"], {}).get(m, 0.0), 2) for m in window]}
              for s in slots]
    series.append({"name": "Other categories", "token": "--chart-7", "values": [round(cell.get("other", {}).get(m, 0.0), 2) for m in window]})
    series.append({"name": "Uncategorised", "token": "--chart-other", "values": [round(cell.get("uncat", {}).get(m, 0.0), 2) for m in window]})
    partial = {c["month"]: not c["complete"] for c in coverage}
    return {"months": window, "labels": [month_short(m) + (" (partial)" if partial.get(m) else "") for m in window],
            "partial": [bool(partial.get(m)) for m in window], "series": series,
            "totals": [round(sum(s["values"][i] for s in series), 2) for i in range(len(window))]}


# ------------------------------------------------------------- other tabs
def recurring_rows(conn: sqlite3.Connection, account_id: int | None) -> dict:
    items = reports.recurring(conn, account_id=account_id)
    for i in items:
        i["type_label"] = TYPE_LABELS.get(i["type_code"], "Card")
        i["last_label"] = day_label(i["last_date"])
    return {"rows": items, "total": sum(i["per_month"] for i in items),
            "fixed_total": sum(i["per_month"] for i in items if i["fixed"]), "count": len(items)}


def target_rows(table: dict) -> list[dict]:
    """Targets tab: every category, with a bullet (last month against the target) once a target exists."""
    out = []
    for c in table["categories"]:
        if c["id"] is None:
            continue
        row = {"id": c["id"], "name": c["name"], "badges": c["badges"], "avg": c["avg"], "last": c["last"],
               "target": c["target"], "over": None, "bullet": None}
        if c["target"] is not None:
            scale = max(c["last"], c["target"], 1.0) * 1.15
            row["over"] = c["last"] - c["target"]
            row["bullet"] = {"actual": round(100 * c["last"] / scale), "tick": round(100 * c["target"] / scale)}
        out.append(row)
    return out


def ideas(conn: sqlite3.Connection, months: list[str], table: dict, account_id: int | None) -> list[dict]:
    """Ranked savings ideas in the glossary's voice: the figure first, the method after."""
    out: list[dict] = []
    if not months:
        return out
    n = len(months)
    acct = f"&account_id={account_id or ''}"
    for c in table["categories"]:
        if c["id"] is not None and c["discretionary"] and c["avg"] >= 50:
            out.append({"title": f"{c['name']}: 20% less saves about {money(c['avg'] * 0.2)}", "saving": c["avg"] * 0.2,
                        "detail": f"Averaging {money(c['avg'])} a month, {c['share']:.0%} of spending.",
                        "href": f"/transactions?category_id={c['id']}{acct}"})
        if c["lumpy"]:
            peak = months[c["series"].index(c["max"])]
            out.append({"title": f"{c['name']}: one unusual month", "saving": 0.0,
                        "detail": f"{month_short(peak)} was {money(c['max'])} against an average of {money(c['avg'])}. "
                                  "If it was a genuine one-off, mark it so the baseline is not distorted.",
                        "href": f"/transactions?category_id={c['id']}{acct}" if c["id"] is not None else "/transactions?uncategorised=1"})
        if c["id"] is None and c["avg"] >= 50:
            out.append({"title": "Categorise the unknowns", "saving": 0.0,
                        "detail": f"{money(c['avg'])} a month is uncategorised; the picture is only as good as this.",
                        "href": "/transactions?uncategorised=1"})
    rec = reports.recurring(conn, account_id=account_id)
    small = [r for r in rec if r["category"] and not r["fixed"] and r["per_month"] < 100]
    if small:
        total = sum(r["per_month"] for r in small)
        named = ", ".join(f"{r['merchant'].title()} ({money(r['per_month'])})" for r in small[:6])
        out.append({"title": f"{len(small)} small repeat payments", "saving": total * 0.3,
                    "detail": f"Subscriptions and repeat purchases under £100 a month add up to {money(total)} a month: {named}"
                              + (" and more." if len(small) > 6 else "."), "href": "#fixed"})
    fixed = [r for r in rec if r["fixed"]]
    if fixed:
        total = sum(r["per_month"] for r in fixed)
        out.append({"title": f"Fixed bills: {money(total)} a month to shop around", "saving": total * 0.05,
                    "detail": "Energy, water, broadband, insurance and the mortgage. These move by switching provider or re-fixing.",
                    "href": "#fixed"})
    merchants = conn.execute(
        f"""SELECT t.description, COALESCE(p.name, c.name) AS top, SUM(-t.amount) AS spend, COUNT(*) AS n
            FROM transactions t {reports.CATEGORY_JOIN}
            WHERE t.amount < 0 AND COALESCE(c.kind, 'expense') = 'expense' AND t.one_off = 0
                  AND substr(t.date, 1, 7) IN ({",".join("?" * n)}) {reports._account_filter(account_id)[0]}
            GROUP BY t.description ORDER BY spend DESC LIMIT 40""",
        [*months, *reports._account_filter(account_id)[1]]).fetchall()
    disc = [m for m in merchants if m["top"] in reports.DISCRETIONARY][:5]
    if disc:
        out.append({"title": "Largest flexible payees", "saving": 0.0,
                    "detail": ", ".join(f"{m['description'][:28].title()} {money(m['spend'] / n)} a month ({m['n']} {'visit' if m['n'] == 1 else 'visits'})"
                                        for m in disc) + ".", "href": "/transactions?month=" + months[-1]})
    out.sort(key=lambda o: -o["saving"])
    return out


def spending_view(conn: sqlite3.Connection, months: list[str], coverage: list[dict], account_id: int | None,
                  exclude_one_off: bool) -> dict:
    """Everything spending.html needs for one period."""
    table = reports.trend_table(conn, months, account_id=account_id, exclude_one_off=exclude_one_off)
    table = decorate_table(add_uncategorised(conn, table, account_id, exclude_one_off))
    window, rows = _window_rows(conn, months, account_id, exclude_one_off)
    uncat = _uncategorised_by_month(conn, window, account_id, exclude_one_off)
    gaps = [c for c in coverage if not c["complete"]]
    targets = target_rows(table)
    set_targets = [t for t in targets if t["target"] is not None]
    return {
        "table": table, "targets": targets, "ideas": ideas(conn, months, table, account_id),
        "fixed": recurring_rows(conn, account_id),
        "multiples": multiples(table, window, rows, coverage), "matrix": matrix(conn, window, rows, coverage, uncat),
        "scope": {"period": span_label(months), "compared": span_label(table["prev_months"]),
                  "compared_short": len(table["prev_months"]) < len(months) and table["prev_months"],
                  "prev_header": dash_label(table["prev_months"]), "gaps": gaps,
                  "gap_text": "; ".join(", ".join(c["gaps"] + [f"{m} missing" for m in c["missing"]]) for c in gaps)},
        "targets_summary": {"count": len(set_targets), "over": sum(1 for t in set_targets if t["over"] and t["over"] > 0),
                            "total": sum(t["target"] for t in set_targets)},
    }


# ---------------------------------------------------------------- net worth
def history(conn: sqlite3.Connection) -> list[dict]:
    """Net worth at each date a balance or valuation was recorded, from holding_snapshots and balance_snapshots.
    Statement closing balances fill in the accounts between manual snapshots. Empty until two dates exist."""
    holds = {r["id"]: r["kind"] for r in conn.execute("SELECT id, kind FROM holdings")}
    hs = conn.execute("SELECT holding_id AS ref, date, value FROM holding_snapshots ORDER BY date, id").fetchall()
    bs = conn.execute("SELECT account_id AS ref, date, balance AS value FROM balance_snapshots ORDER BY date, id").fetchall()
    st = conn.execute("SELECT account_id AS ref, period_end AS date, closing_balance AS value FROM statements "
                      "WHERE closing_balance IS NOT NULL ORDER BY period_end, id").fetchall()
    dates = sorted({r["date"] for r in hs} | {r["date"] for r in bs})
    if len(dates) < 2:
        return []
    events = sorted([(r["date"], 0, ("a", r["ref"]), r["value"]) for r in st]
                    + [(r["date"], 1, ("a", r["ref"]), r["value"]) for r in bs]
                    + [(r["date"], 2, ("h", r["ref"]), r["value"]) for r in hs])
    latest: dict = {}
    points, i = [], 0
    for d in dates:
        while i < len(events) and events[i][0] <= d:
            _, _, key, value = events[i]
            latest[key] = value
            i += 1
        total = sum((-v if (k[0] == "h" and holds.get(k[1]) in reports.LIABILITY_KINDS) else v) for k, v in latest.items())
        points.append({"date": d, "label": day_label(d), "value": round(total, 2)})
    return points


def networth_view(conn: sqlite3.Connection, today: date | None = None) -> dict:
    from . import household

    today = today or date.today()
    nw = reports.net_worth(conn)
    owners = household.owner_choices(conn)
    for a in nw["accounts"]:
        age = (today - date.fromisoformat(a["date"])).days if a["date"] else None
        a["age"] = age
        a["stale"] = age is None or age > review.STALE_DAYS
        a["updated"] = day_label(a["date"]) if a["date"] else "Never"
        a["owner_label"] = household.owner_label(owners, a["owner"])
    counts = {r["holding_id"]: r["n"] for r in conn.execute("SELECT holding_id, COUNT(*) AS n FROM holding_snapshots GROUP BY holding_id")}
    for h in nw["holdings"]:
        h["n_snapshots"] = counts.get(h["id"], 0)
        h["owner_label"] = household.owner_label(owners, h["owner"])
        h["valued_label"] = day_label(h["valued_at"])
    by_parent: dict = {}
    for h in nw["holdings"]:
        by_parent.setdefault(h["parent_id"], []).append(h)
    ordered = []
    for h in sorted(by_parent.get(None, []), key=lambda h: (h["kind"] != "property", h["is_liability"], -h["value"])):
        ordered.append(h)
        ordered.extend(by_parent.get(h["id"], []))
    known = {h["id"] for h in ordered}
    nw["holdings"] = ordered + [h for h in nw["holdings"] if h["id"] not in known]
    holds = nw["holdings"]
    isas = sum(h["value"] for h in holds if h["kind"] in ("cash_isa", "stocks_isa"))
    stocks = sum(h["value"] for h in holds if h["kind"] == "stocks")
    cash = nw["cash_in_accounts"] + sum(h["value"] for h in holds if h["kind"] == "cash")
    other_assets = sum(h["value"] for h in holds if h["kind"] in ("pension", "other_asset"))
    other_debt = sum(h["value"] for h in holds if h["kind"] in ("loan", "other_liability"))
    parts = [p for p in [{"name": "Property equity", "token": "--ramp-1", "value": nw["equity"]},
                         {"name": "ISAs", "token": "--ramp-2", "value": isas},
                         {"name": "Stocks", "token": "--ramp-3", "value": stocks},
                         {"name": "Cash and pots", "token": "--ramp-4", "value": cash},
                         {"name": "Pensions and other", "token": "--chart-other", "value": other_assets}] if p["value"]]
    debts = [p for p in [{"name": "Mortgage", "token": "--color-negative", "value": -nw["mortgage"]},
                         {"name": "Other debts", "token": "--color-negative", "value": -other_debt}] if p["value"]]
    own = sum(p["value"] for p in parts)
    for p in parts:
        p["share"] = (p["value"] / own) if own else 0.0
    ltv = round(nw["ltv"] * 100) if nw["ltv"] is not None else None
    next_band = next((b for b in LTV_BANDS if ltv is not None and ltv < b), None)
    hist = history(conn)
    delta = (hist[-1]["value"] - hist[-2]["value"]) if len(hist) >= 2 else None
    mortgages = nw["mortgages"]
    m = mortgages[0] if mortgages else None
    stale = [a for a in nw["accounts"] if a["stale"]]
    return {
        **nw, "stale": stale, "parts": parts, "debts": debts, "own": own,
        "ltv_pct": ltv, "next_band": next_band, "history": hist, "delta": delta,
        "snapshot_dates": sorted({r[0] for r in conn.execute("SELECT date FROM holding_snapshots UNION SELECT date FROM balance_snapshots")}),
        "mortgage_main": m,
        "label": {"total": compact(nw["total"]), "liquid": compact(nw["liquid"]), "equity": compact(nw["equity"]),
                  "mortgage": compact(nw["mortgage"])},
        "chart_label": "Composition of net worth: " + ", ".join(f"{p['name'].lower()} {money(p['value'])}" for p in parts)
                       + ("; " + ", ".join(f"{d['name'].lower()} {money(-d['value'])}" for d in debts) if debts else ""),
    }
