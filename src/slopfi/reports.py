"""Report queries. All functions take a sqlite3 connection and return plain rows/dicts."""
from __future__ import annotations

import sqlite3
from collections import defaultdict

CATEGORY_JOIN = """
    LEFT JOIN categories c ON c.id = t.category_id
    LEFT JOIN categories p ON p.id = c.parent_id
"""


def money_whole(value: float | None) -> str:
    """Whole pounds with a true minus: the tile and note format."""
    if value is None:
        return "—"
    return f"{'−' if value < 0 else ''}£{abs(value):,.0f}"


def months_available(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT substr(date, 1, 7) AS m FROM transactions ORDER BY m DESC").fetchall()
    return [r["m"] for r in rows]


def accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT a.*, (SELECT COUNT(*) FROM transactions t WHERE t.account_id = a.id) AS n_txn,
                  (SELECT MIN(date) FROM transactions t WHERE t.account_id = a.id) AS first_date,
                  (SELECT MAX(date) FROM transactions t WHERE t.account_id = a.id) AS last_date
           FROM accounts a ORDER BY a.owner, a.name"""
    ).fetchall()


def _month_filter(month: str | None) -> tuple[str, list]:
    if month and month != "all":
        return "AND substr(t.date, 1, 7) = ?", [month]
    return "", []


def _account_filter(account_id: int | None) -> tuple[str, list]:
    if account_id:
        return "AND t.account_id = ?", [account_id]
    return "", []


def monthly_summary(conn: sqlite3.Connection, account_id: int | None = None, exclude_one_off: bool = False) -> list[dict]:
    """Per calendar month: outgoings, income, transfers, uncategorised (transfers excluded from net)."""
    aclause, aparams = _account_filter(account_id)
    if exclude_one_off:
        aclause += " AND t.one_off = 0"
    rows = conn.execute(
        f"""
        SELECT substr(t.date, 1, 7) AS month,
               COALESCE(c.kind, CASE WHEN t.amount < 0 THEN 'expense' ELSE 'income' END) AS k,
               (t.category_id IS NULL) AS uncategorised,
               (t.amount < 0) AS neg,
               SUM(t.amount) AS total, COUNT(*) AS n
        FROM transactions t {CATEGORY_JOIN}
        WHERE 1=1 {aclause}
        GROUP BY month, k, uncategorised, neg
        ORDER BY month
        """,
        aparams,
    ).fetchall()
    months: dict[str, dict] = {}
    for r in rows:
        m = months.setdefault(
            r["month"],
            {"month": r["month"], "outgoings": 0.0, "income": 0.0, "transfers_in": 0.0,
             "transfers_out": 0.0, "uncategorised_amount": 0.0, "uncategorised_count": 0, "count": 0},
        )
        m["count"] += r["n"]
        if r["k"] == "expense":
            m["outgoings"] += -r["total"]
        elif r["k"] == "income":
            m["income"] += r["total"]
        else:
            if r["total"] >= 0:
                m["transfers_in"] += r["total"]
            else:
                m["transfers_out"] += -r["total"]
        if r["uncategorised"]:
            m["uncategorised_amount"] += abs(r["total"])
            m["uncategorised_count"] += r["n"]
    for m in months.values():
        m["net"] = m["income"] - m["outgoings"]
    return [months[k] for k in sorted(months)]


def category_breakdown(conn: sqlite3.Connection, month: str | None, account_id: int | None = None) -> list[dict]:
    """Spend per top-level category (with children) for the month, uncategorised included."""
    clause, params = _month_filter(month)
    aclause, aparams = _account_filter(account_id)
    clause, params = clause + " " + aclause, params + aparams
    rows = conn.execute(
        f"""
        SELECT COALESCE(p.id, c.id) AS top_id,
               COALESCE(p.name, c.name, 'Uncategorised') AS top_name,
               CASE WHEN p.id IS NULL THEN NULL ELSE c.id END AS sub_id,
               CASE WHEN p.id IS NULL THEN NULL ELSE c.name END AS sub_name,
               COALESCE(c.kind, 'expense') AS kind,
               SUM(-t.amount) AS spend, COUNT(*) AS n
        FROM transactions t {CATEGORY_JOIN}
        WHERE (c.kind = 'expense' OR t.category_id IS NULL) {clause}
        GROUP BY top_id, sub_id
        """,
        params,
    ).fetchall()
    tops: dict = {}
    for r in rows:
        top = tops.setdefault(r["top_id"], {"id": r["top_id"], "name": r["top_name"], "spend": 0.0, "n": 0, "children": []})
        top["spend"] += r["spend"]
        top["n"] += r["n"]
        if r["sub_id"] is not None:
            top["children"].append({"id": r["sub_id"], "name": r["sub_name"], "spend": r["spend"], "n": r["n"]})
    result = sorted(tops.values(), key=lambda x: -x["spend"])
    for top in result:
        top["children"].sort(key=lambda x: -x["spend"])
    return result


def category_month_matrix(conn: sqlite3.Connection, limit_months: int = 12, account_id: int | None = None) -> dict:
    """Spend by top-level category per month, for the stacked trend chart."""
    aclause, aparams = _account_filter(account_id)
    rows = conn.execute(
        f"""
        SELECT substr(t.date, 1, 7) AS month,
               COALESCE(p.name, c.name, 'Uncategorised') AS top_name,
               SUM(-t.amount) AS spend
        FROM transactions t {CATEGORY_JOIN}
        WHERE (c.kind = 'expense' OR t.category_id IS NULL) {aclause}
        GROUP BY month, top_name ORDER BY month
        """,
        aparams,
    ).fetchall()
    months = sorted({r["month"] for r in rows})[-limit_months:]
    totals: dict[str, float] = defaultdict(float)
    cell: dict[tuple[str, str], float] = defaultdict(float)
    for r in rows:
        if r["month"] in months:
            cell[(r["month"], r["top_name"])] += r["spend"]
            totals[r["top_name"]] += r["spend"]
    categories = sorted(totals, key=lambda k: -totals[k])
    return {
        "months": months,
        "series": [{"name": cat, "values": [round(cell[(m, cat)], 2) for m in months]} for cat in categories],
    }


def top_merchants(conn: sqlite3.Connection, month: str | None, limit: int = 15, account_id: int | None = None) -> list[sqlite3.Row]:
    clause, params = _month_filter(month)
    aclause, aparams = _account_filter(account_id)
    clause, params = clause + " " + aclause, params + aparams
    return conn.execute(
        f"""
        SELECT t.description, COALESCE(p.name || ' / ' || c.name, c.name) AS category,
               SUM(-t.amount) AS spend, COUNT(*) AS n
        FROM transactions t {CATEGORY_JOIN}
        WHERE t.amount < 0 AND COALESCE(c.kind, 'expense') = 'expense' {clause}
        GROUP BY t.description ORDER BY spend DESC LIMIT ?
        """,
        [*params, limit],
    ).fetchall()


def recurring(conn: sqlite3.Connection, account_id: int | None = None) -> list[dict]:
    """Payments that look recurring: same merchant key in >= 2 distinct months.

    DD/SO always count; card payments count when the amount is stable (within 15%).
    """
    aclause, aparams = _account_filter(account_id)
    rows = conn.execute(
        f"""
        SELECT t.id, t.date, t.type_code, t.description, t.amount,
               COALESCE(p.name || ' / ' || c.name, c.name) AS category, COALESCE(c.kind, '') AS kind
        FROM transactions t {CATEGORY_JOIN}
        WHERE t.amount < 0 AND COALESCE(c.kind, 'expense') = 'expense' {aclause}
        ORDER BY t.date
        """,
        aparams,
    ).fetchall()
    from .categorise import suggest_pattern

    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        key = (suggest_pattern(r["description"]), r["type_code"] or "")
        groups[key].append(r)
    out = []
    for (merchant, type_code), items in groups.items():
        months = sorted({r["date"][:7] for r in items})
        if len(months) < 2:
            continue
        amounts = [-r["amount"] for r in items]
        avg = sum(amounts) / len(amounts)
        stable = max(amounts) - min(amounts) <= max(0.15 * avg, 1.0)
        fixed = type_code in ("DD", "SO")
        if not (fixed or stable):
            continue
        out.append({
            "merchant": merchant, "type_code": type_code, "category": items[-1]["category"],
            "months": len(months), "count": len(items), "avg": avg, "last": amounts[-1],
            "last_date": items[-1]["date"], "per_month": sum(amounts) / len(months),
            "fixed": fixed,
        })
    out.sort(key=lambda x: -x["per_month"])
    return out


def transactions(
    conn: sqlite3.Connection,
    month: str | None = None,
    category_id: int | None = None,
    q: str | None = None,
    uncategorised: bool = False,
    account_id: int | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    where = ["1=1"]
    params: list = []
    clause, p = _month_filter(month)
    if clause:
        where.append(clause[4:])
        params += p
    if category_id is not None:
        where.append("(t.category_id = ? OR p.id = ?)")
        params += [category_id, category_id]
    if uncategorised:
        where.append("t.category_id IS NULL")
    if q:
        where.append("t.description LIKE ?")
        params.append(f"%{q}%")
    if account_id is not None:
        where.append("t.account_id = ?")
        params.append(account_id)
    sql = f"""
        SELECT t.*, c.name AS category_name, p.name AS parent_name, c.kind AS category_kind,
               a.name AS account_name, r.pattern AS rule_pattern
        FROM transactions t {CATEGORY_JOIN}
        JOIN accounts a ON a.id = t.account_id
        LEFT JOIN rules r ON r.id = t.rule_id
        WHERE {' AND '.join(where)}
        ORDER BY t.date DESC, t.statement_id DESC, t.seq DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def transaction(conn: sqlite3.Connection, transaction_id: int) -> sqlite3.Row | None:
    rows = conn.execute(
        f"""
        SELECT t.*, c.name AS category_name, p.name AS parent_name, c.kind AS category_kind,
               a.name AS account_name, r.pattern AS rule_pattern
        FROM transactions t {CATEGORY_JOIN}
        JOIN accounts a ON a.id = t.account_id
        LEFT JOIN rules r ON r.id = t.rule_id
        WHERE t.id = ?
        """,
        (transaction_id,),
    ).fetchall()
    return rows[0] if rows else None


def categories_tree(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT c.*, (SELECT COUNT(*) FROM transactions t WHERE t.category_id = c.id) AS n_txn,
                  (SELECT COUNT(*) FROM rules r WHERE r.category_id = c.id) AS n_rules
           FROM categories c ORDER BY c.sort_order, c.name"""
    ).fetchall()
    tops = [dict(r, children=[]) for r in rows if r["parent_id"] is None]
    by_id = {t["id"]: t for t in tops}
    for r in rows:
        if r["parent_id"] is not None and r["parent_id"] in by_id:
            by_id[r["parent_id"]]["children"].append(dict(r))
    return tops


def category_options(conn: sqlite3.Connection) -> list[dict]:
    """Flat list for <select>: id, label ('Parent / Child'), kind."""
    out = []
    for top in categories_tree(conn):
        if not top["children"]:
            out.append({"id": top["id"], "label": top["name"], "kind": top["kind"]})
        for ch in top["children"]:
            out.append({"id": ch["id"], "label": f"{top['name']} / {ch['name']}", "kind": top["kind"]})
    return out


# ----------------------------------------------------------------- trends
import calendar
from datetime import date

DISCRETIONARY = {"Eating out", "Shopping", "Leisure", "Cash", "Other spending", "Work & side business"}


def coverage(conn: sqlite3.Connection) -> list[dict]:
    """First and last date each account has data for (statement periods or transactions)."""
    rows = conn.execute(
        """SELECT a.id, a.name, a.kind,
                  MIN(COALESCE(s.period_start, t.date)) AS first, MAX(COALESCE(s.period_end, t.date)) AS last
           FROM accounts a
           LEFT JOIN statements s ON s.account_id = a.id
           LEFT JOIN transactions t ON t.statement_id = s.id
           GROUP BY a.id ORDER BY a.name"""
    ).fetchall()
    return [dict(r) for r in rows if r["first"]]


def month_coverage(conn: sqlite3.Connection) -> list[dict]:
    """For each month with data: whether every spending account (not savings) covers all or part of it."""
    accts = [a for a in coverage(conn) if a["kind"] != "savings"]
    out = []
    for m in sorted(months_available(conn)):
        y, mo = int(m[:4]), int(m[5:7])
        start, end = date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1])
        gaps, missing = [], []
        for a in accts:
            first, last = date.fromisoformat(a["first"]), date.fromisoformat(a["last"])
            if first > end or last < start:
                missing.append(a["name"])
            elif first > start or last < end:
                gaps.append(f"{a['name']} {first.strftime('%-d %b') if first > start else ''}"
                            f"{'–' if first > start and last < end else ''}"
                            f"{('to ' + last.strftime('%-d %b')) if last < end else ''}".replace("  ", " ").strip())
        out.append({"month": m, "complete": not gaps and not missing, "covered": not missing,
                    "gaps": gaps, "missing": missing})
    return out


def trend_months(conn: sqlite3.Connection, n: int, include_partial: bool = True) -> tuple[list[str], list[dict]]:
    cov = month_coverage(conn)
    usable = [c for c in cov if c["covered"] and (include_partial or c["complete"])]
    chosen = [c["month"] for c in usable][-n:]
    return chosen, [c for c in cov if c["month"] in chosen]


def _spend_by_category_month(conn, months, account_id, exclude_one_off):
    if not months:
        return []
    aclause, aparams = _account_filter(account_id)
    one_off = "AND t.one_off = 0" if exclude_one_off else ""
    placeholders = ",".join("?" * len(months))
    return conn.execute(
        f"""
        SELECT substr(t.date, 1, 7) AS month,
               COALESCE(p.id, c.id) AS top_id, COALESCE(p.name, c.name, 'Uncategorised') AS top_name,
               CASE WHEN p.id IS NULL THEN NULL ELSE c.id END AS sub_id,
               CASE WHEN p.id IS NULL THEN NULL ELSE c.name END AS sub_name,
               SUM(-t.amount) AS spend,
               SUM(CASE WHEN t.type_code IN ('DD', 'SO') THEN -t.amount ELSE 0 END) AS fixed,
               COUNT(*) AS n
        FROM transactions t {CATEGORY_JOIN}
        WHERE (c.kind = 'expense' OR t.category_id IS NULL) AND substr(t.date, 1, 7) IN ({placeholders})
              {aclause} {one_off}
        GROUP BY month, top_id, sub_id
        """,
        [*months, *aparams],
    ).fetchall()


def _series(rows, months, key):
    """rows -> {key: {month: spend}} aggregated."""
    out: dict = {}
    for r in rows:
        k = key(r)
        if k is None:
            continue
        d = out.setdefault(k, {"name": None, "id": None, "values": {m: 0.0 for m in months}, "fixed": 0.0, "n": 0})
        d["values"][r["month"]] += r["spend"]
        d["fixed"] += r["fixed"]
        d["n"] += r["n"]
    return out


def _stats(values: list[float]) -> dict:
    n = len(values) or 1
    avg = sum(values) / n
    return {"avg": avg, "min": min(values) if values else 0.0, "max": max(values) if values else 0.0,
            "last": values[-1] if values else 0.0, "total": sum(values)}


def trend_table(conn: sqlite3.Connection, months: list[str], account_id: int | None = None,
                exclude_one_off: bool = True) -> dict:
    """Spend per top-level category (and sub-category) per month, with averages and the change
    against the window of the same length immediately before."""
    all_months = sorted(months_available(conn))
    prev_months = [m for m in all_months if m < (months[0] if months else "9999")][-len(months):] if months else []

    rows = _spend_by_category_month(conn, months, account_id, exclude_one_off)
    prev_rows = _spend_by_category_month(conn, prev_months, account_id, exclude_one_off)

    tops = _series(rows, months, lambda r: r["top_id"])
    prev_tops = _series(prev_rows, prev_months, lambda r: r["top_id"])
    subs = _series(rows, months, lambda r: (r["top_id"], r["sub_id"]) if r["sub_id"] is not None else None)
    names = {r["top_id"]: r["top_name"] for r in rows}
    sub_names = {(r["top_id"], r["sub_id"]): r["sub_name"] for r in rows if r["sub_id"] is not None}

    budgets_by_cat = budgets(conn)
    total_avg = sum(sum(d["values"].values()) for d in tops.values()) / (len(months) or 1)
    categories = []
    for top_id, d in tops.items():
        values = [d["values"][m] for m in months]
        st = _stats(values)
        prev = prev_tops.get(top_id)
        prev_avg = (sum(prev["values"].values()) / len(prev_months)) if prev and prev_months else None
        change = (st["avg"] - prev_avg) if prev_avg is not None else None
        budget = budgets_by_cat.get(top_id)
        children = []
        for (t_id, s_id), sd in subs.items():
            if t_id != top_id:
                continue
            svals = [sd["values"][m] for m in months]
            sst = _stats(svals)
            children.append({"id": s_id, "name": sub_names[(t_id, s_id)], "series": svals, **sst,
                             "fixed_share": (sd["fixed"] / sst["total"]) if sst["total"] else 0.0, "n": sd["n"]})
        children.sort(key=lambda c: -c["avg"])
        categories.append({
            "id": top_id, "name": names[top_id], "series": values, **st,
            "share": (st["avg"] / total_avg) if total_avg else 0.0,
            "fixed_share": (d["fixed"] / st["total"]) if st["total"] else 0.0,
            "prev_avg": prev_avg, "change": change,
            "change_pct": (change / prev_avg) if change is not None and prev_avg else None,
            "lumpy": st["max"] > 2 * st["avg"] and st["avg"] > 25,
            "discretionary": names[top_id] in DISCRETIONARY,
            "target": budget["monthly_target"] if budget else None,
            "gap": (st["avg"] - budget["monthly_target"]) if budget else None,
            "children": children, "n": d["n"],
        })
    categories.sort(key=lambda c: -c["avg"])

    summary = {m: s for m, s in ((x["month"], x) for x in monthly_summary(conn, account_id=account_id, exclude_one_off=exclude_one_off)) if m in months}
    totals = {
        "months": months,
        "outgoings": [summary.get(m, {}).get("outgoings", 0.0) for m in months],
        "income": [summary.get(m, {}).get("income", 0.0) for m in months],
    }
    totals["net"] = [i - o for i, o in zip(totals["income"], totals["outgoings"])]
    totals["avg_outgoings"] = sum(totals["outgoings"]) / (len(months) or 1)
    totals["avg_income"] = sum(totals["income"]) / (len(months) or 1)
    totals["avg_net"] = totals["avg_income"] - totals["avg_outgoings"]
    totals["savings_rate"] = (totals["avg_net"] / totals["avg_income"]) if totals["avg_income"] else None
    totals["trend_avg_outgoings"] = total_avg   # expense categories only, one-offs excluded if requested
    target_total = sum(c["target"] for c in categories if c["target"] is not None)
    return {"months": months, "prev_months": prev_months, "categories": categories, "totals": totals,
            "target_total": target_total, "targeted_avg": sum(c["avg"] for c in categories if c["target"] is not None)}


def savings_opportunities(conn: sqlite3.Connection, months: list[str], table: dict,
                          account_id: int | None = None) -> list[dict]:
    """Heuristic, ranked suggestions for where savings are most likely. Amounts are per month."""
    opps: list[dict] = []
    if not months:
        return opps
    n = len(months)
    for c in table["categories"]:
        if c["discretionary"] and c["avg"] >= 50:
            opps.append({
                "kind": "discretionary", "title": f"{c['name']}: trim by 20%",
                "detail": f"Averaging {c['avg']:,.0f}/month ({c['share']:.0%} of spend). A fifth less is a realistic behavioural target.",
                "saving": c["avg"] * 0.2, "link": f"/transactions?category_id={c['id']}&account_id={account_id or ''}",
            })
        if c["lumpy"]:
            opps.append({
                "kind": "lumpy", "title": f"{c['name']}: check the spike",
                "detail": f"Peaked at {c['max']:,.0f} against an average of {c['avg']:,.0f}. If it was a genuine one-off, mark it so the baseline isn't distorted.",
                "saving": 0.0, "link": f"/transactions?category_id={c['id']}&account_id={account_id or ''}",
            })
        if c["name"] == "Uncategorised" and c["avg"] >= 50:
            opps.append({
                "kind": "data", "title": "Categorise the unknowns",
                "detail": f"{c['avg']:,.0f}/month is uncategorised; the picture above is only as good as this.",
                "saving": 0.0, "link": "/transactions?uncategorised=1",
            })
    rec = [r for r in recurring(conn, account_id=account_id) if r["category"] and not r["fixed"] and r["per_month"] < 100]
    if rec:
        total = sum(r["per_month"] for r in rec)
        opps.append({
            "kind": "subscriptions", "title": f"Review {len(rec)} small recurring card payments",
            "detail": "Subscriptions and repeat purchases under 100/month add up to " + f"{total:,.0f}/month: " +
                      ", ".join(f"{r['merchant']} ({r['per_month']:,.0f})" for r in rec[:6]) + ("…" if len(rec) > 6 else ""),
            "saving": total * 0.3, "link": "/recurring",
        })
    fixed = [r for r in recurring(conn, account_id=account_id) if r["fixed"]]
    if fixed:
        total = sum(r["per_month"] for r in fixed)
        opps.append({
            "kind": "fixed", "title": f"Renegotiate fixed bills ({total:,.0f}/month)",
            "detail": "Direct debits and standing orders: energy, water, broadband, insurance and the mortgage. "
                      "These move by switching provider or re-fixing, not by day-to-day behaviour.",
            "saving": total * 0.05, "link": "/recurring",
        })
    merchants = conn.execute(
        f"""SELECT t.description, COALESCE(p.name, c.name) AS top, SUM(-t.amount) AS spend, COUNT(*) AS n
            FROM transactions t {CATEGORY_JOIN}
            WHERE t.amount < 0 AND COALESCE(c.kind, 'expense') = 'expense' AND t.one_off = 0
                  AND substr(t.date, 1, 7) IN ({",".join("?" * n)}) {_account_filter(account_id)[0]}
            GROUP BY t.description ORDER BY spend DESC LIMIT 40""",
        [*months, *_account_filter(account_id)[1]],
    ).fetchall()
    disc = [m for m in merchants if m["top"] in DISCRETIONARY][:5]
    if disc:
        opps.append({
            "kind": "merchants", "title": "Biggest discretionary merchants",
            "detail": "; ".join(f"{m['description'][:28]} {m['spend'] / n:,.0f}/month over {m['n']} visits" for m in disc),
            "saving": 0.0, "link": "/",
        })
    opps.sort(key=lambda o: -o["saving"])
    return opps


def budgets(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    return {r["category_id"]: r for r in conn.execute("SELECT * FROM budgets")}


def set_budget(conn: sqlite3.Connection, category_id: int, target: float | None, note: str | None = None) -> None:
    from .db import now_iso

    with conn:
        if target is None:
            conn.execute("DELETE FROM budgets WHERE category_id = ?", (category_id,))
        else:
            conn.execute(
                """INSERT INTO budgets (category_id, monthly_target, note, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(category_id) DO UPDATE SET monthly_target = excluded.monthly_target,
                   note = excluded.note, updated_at = excluded.updated_at""",
                (category_id, target, note, now_iso()),
            )


def set_one_off(conn: sqlite3.Connection, transaction_id: int, flag: bool) -> None:
    with conn:
        conn.execute("UPDATE transactions SET one_off = ? WHERE id = ?", (1 if flag else 0, transaction_id))


# -------------------------------------------------------------- net worth
LIABILITY_KINDS = {"mortgage", "loan", "other_liability"}
KIND_LABELS = {
    "property": "Property", "mortgage": "Mortgage", "pension": "Pension", "stocks": "Stocks & shares",
    "stocks_isa": "Stocks & shares ISA", "cash_isa": "Cash ISA", "cash": "Cash savings", "loan": "Loan",
    "other_asset": "Other asset", "other_liability": "Other liability",
}
LIQUID_KINDS = {"cash", "cash_isa", "stocks", "stocks_isa"}


def account_balances(conn: sqlite3.Connection) -> list[dict]:
    """Latest known balance per account: the newest statement closing balance or manual snapshot."""
    rows = conn.execute(
        """SELECT a.id, a.name, a.kind, a.owner,
                  (SELECT closing_balance FROM statements s WHERE s.account_id = a.id AND s.closing_balance IS NOT NULL
                   ORDER BY s.period_end DESC LIMIT 1) AS stmt_balance,
                  (SELECT period_end FROM statements s WHERE s.account_id = a.id AND s.closing_balance IS NOT NULL
                   ORDER BY s.period_end DESC LIMIT 1) AS stmt_date,
                  (SELECT balance FROM balance_snapshots b WHERE b.account_id = a.id ORDER BY b.date DESC, b.id DESC LIMIT 1) AS snap_balance,
                  (SELECT date FROM balance_snapshots b WHERE b.account_id = a.id ORDER BY b.date DESC, b.id DESC LIMIT 1) AS snap_date
           FROM accounts a ORDER BY a.owner, a.name"""
    ).fetchall()
    out = []
    for r in rows:
        candidates = [(d, b, src) for d, b, src in ((r["stmt_date"], r["stmt_balance"], "statement"),
                                                     (r["snap_date"], r["snap_balance"], "manual")) if d]
        date_, balance, source = max(candidates) if candidates else (None, None, None)
        out.append({"id": r["id"], "name": r["name"], "kind": r["kind"], "owner": r["owner"],
                    "balance": balance, "date": date_, "source": source})
    return out


def holdings(conn: sqlite3.Connection) -> list[dict]:
    """All holdings, with a linked mortgage listed directly after its property."""
    rows = conn.execute("SELECT * FROM holdings ORDER BY kind, name").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["is_liability"] = r["kind"] in LIABILITY_KINDS
        d["kind_label"] = KIND_LABELS.get(r["kind"], r["kind"])
        d["signed"] = -r["value"] if d["is_liability"] else r["value"]
        out.append(d)
    by_parent: dict = {}
    for d in out:
        if d["parent_id"]:
            by_parent.setdefault(d["parent_id"], []).append(d)
    ordered = []
    for d in out:
        if d["parent_id"]:
            continue
        ordered.append(d)
        if d["kind"] == "property":
            linked = by_parent.get(d["id"], [])
            d["mortgage"] = linked[0] if linked else None
            d["equity"] = d["value"] - sum(m["value"] for m in linked)
            ordered.extend(linked)
    return ordered


def net_worth(conn: sqlite3.Connection) -> dict:
    accts = account_balances(conn)
    holds = holdings(conn)
    cash_in_accounts = sum(a["balance"] or 0.0 for a in accts)
    assets = sum(h["value"] for h in holds if not h["is_liability"])
    liabilities = sum(h["value"] for h in holds if h["is_liability"])
    property_value = sum(h["value"] for h in holds if h["kind"] == "property")
    mortgage = sum(h["value"] for h in holds if h["kind"] == "mortgage")
    liquid = cash_in_accounts + sum(h["value"] for h in holds if h["kind"] in LIQUID_KINDS)
    mortgages = [h for h in holds if h["kind"] == "mortgage"]
    for m in mortgages:
        m["monthly_interest"] = (m["value"] * (m["rate"] or 0) / 100) / 12
        m["monthly_capital"] = (m["monthly_payment"] - m["monthly_interest"]) if m["monthly_payment"] else None
    return {
        "accounts": accts, "holdings": holds,
        "cash_in_accounts": cash_in_accounts, "assets": assets, "liabilities": liabilities,
        "total": cash_in_accounts + assets - liabilities,
        "property_value": property_value, "mortgage": mortgage, "equity": property_value - mortgage,
        "ltv": (mortgage / property_value) if property_value else None,
        "liquid": liquid, "mortgages": mortgages,
        "unknown_accounts": [a for a in accts if a["balance"] is None],
    }


def save_holding(conn: sqlite3.Connection, data: dict, holding_id: int | None = None) -> int:
    from .db import now_iso

    fields = ("name", "kind", "owner", "provider", "value", "valued_at", "rate", "monthly_payment", "fix_end", "term_end", "notes")
    values = [data.get(f) for f in fields]
    with conn:
        if holding_id is None:
            cur = conn.execute(
                f"INSERT INTO holdings ({', '.join(fields)}, created_at) VALUES ({', '.join('?' * len(fields))}, ?)",
                [*values, now_iso()],
            )
            holding_id = cur.lastrowid
        else:
            conn.execute(f"UPDATE holdings SET {', '.join(f + ' = ?' for f in fields)} WHERE id = ?", [*values, holding_id])
        conn.execute(
            "INSERT INTO holding_snapshots (holding_id, date, value) VALUES (?, ?, ?) "
            "ON CONFLICT(holding_id, date) DO UPDATE SET value = excluded.value",
            (holding_id, data["valued_at"], data["value"]),
        )
    return holding_id


def save_property(conn: sqlite3.Connection, data: dict, mortgage: dict | None, holding_id: int | None = None) -> int:
    """Save a property and, if a balance is given, its linked mortgage (created, updated or removed)."""
    data = {**data, "kind": "property", "rate": None, "monthly_payment": None, "fix_end": None, "term_end": None}
    prop_id = save_holding(conn, data, holding_id)
    existing = conn.execute("SELECT id FROM holdings WHERE parent_id = ? AND kind = 'mortgage'", (prop_id,)).fetchone()
    if mortgage and mortgage.get("value"):
        m = {"name": f"Mortgage: {data['name']}", "kind": "mortgage", "owner": data["owner"], "provider": mortgage.get("provider"),
             "value": mortgage["value"], "valued_at": data["valued_at"], "rate": mortgage.get("rate"),
             "monthly_payment": mortgage.get("monthly_payment"), "fix_end": mortgage.get("fix_end"),
             "term_end": mortgage.get("term_end"), "notes": None}
        mid = save_holding(conn, m, existing["id"] if existing else None)
        with conn:
            conn.execute("UPDATE holdings SET parent_id = ? WHERE id = ?", (prop_id, mid))
    elif existing:
        delete_holding(conn, existing["id"])
    return prop_id


def delete_holding(conn: sqlite3.Connection, holding_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))


def add_balance_snapshot(conn: sqlite3.Connection, account_id: int, date_: str, balance: float) -> None:
    with conn:
        conn.execute(
            "INSERT INTO balance_snapshots (account_id, date, balance, source) VALUES (?, ?, ?, 'manual') "
            "ON CONFLICT(account_id, date, source) DO UPDATE SET balance = excluded.balance",
            (account_id, date_, balance),
        )
