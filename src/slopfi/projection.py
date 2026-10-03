"""Net worth projection: scenarios, observed defaults, and a pure monthly engine."""
from __future__ import annotations

import json
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import date

from . import reports
from .db import get_setting, now_iso, set_setting

# Settings keys with their defaults (annual %, except horizon). Editable on the Settings page.
SETTING_DEFAULTS: dict[str, tuple[float, str]] = {
    "proj.inflation": (3.1, "Inflation, % a year"),
    "proj.horizon_years": (10, "Horizon, years"),
    "proj.rate.current": (0.0, "Current accounts, interest %"),
    "proj.rate.savings": (3.0, "Savings pots, interest %"),
    "proj.rate.credit_card": (0.0, "Credit cards, interest %"),
    "proj.rate.cash": (4.0, "Cash savings, interest %"),
    "proj.rate.cash_isa": (4.5, "Cash ISA, interest %"),
    "proj.rate.stocks": (5.0, "Stocks and shares, growth %"),
    "proj.rate.stocks_isa": (5.0, "Stocks and shares ISA, growth %"),
    "proj.rate.pension": (5.0, "Pensions, growth %"),
    "proj.rate.property": (3.0, "Property, price growth %"),
    "proj.rate.other_asset": (0.0, "Other assets, growth %"),
    "proj.rate.loan": (0.0, "Loans, interest %"),
    "proj.rate.other_liability": (0.0, "Other liabilities, interest %"),
}
# Shown as help text under the field on the Settings page.
SETTING_HELP: dict[str, str] = {
    "proj.inflation": "Used for the in today's money line.",
    "proj.horizon_years": "How far each new scenario looks ahead.",
    "proj.rate.savings": "Used only when no interest has been observed on the pot.",
    "proj.rate.credit_card": "Balances are assumed cleared each month.",
    "proj.rate.loan": "The balance falls by the monthly payment.",
    "proj.rate.cash": "Also used for savings from the levers when no destination is chosen.",
}
ASSET_CLASS = {
    "current": "Cash and pots", "savings": "Cash and pots", "cash": "Cash and pots",
    "cash_isa": "ISAs", "stocks_isa": "ISAs", "stocks": "Stocks", "pension": "Pensions",
    "property": "Property equity", "other_asset": "Other", "credit_card": "Cash and pots",
    "loan": "Other", "other_liability": "Other", "mortgage": "Property equity",
}
# Bands of the stacked chart, bottom to top: least liquid first, so the top edge is net worth and cash sits on top.
CLASS_ORDER = ["Property equity", "Pensions", "ISAs", "Stocks", "Other", "Cash and pots"]
MILESTONE_YEARS = (1, 3, 5, 10)


def setting(conn: sqlite3.Connection, key: str) -> float:
    raw = get_setting(conn, key)
    return float(raw) if raw not in (None, "") else SETTING_DEFAULTS[key][0]


def all_settings(conn: sqlite3.Connection) -> list[dict]:
    return [{"key": k, "value": setting(conn, k), "default": d, "label": label, "help": SETTING_HELP.get(k, ""),
             "unit": "years" if k == "proj.horizon_years" else "%"} for k, (d, label) in SETTING_DEFAULTS.items()]


def save_settings(conn: sqlite3.Connection, values: dict[str, str]) -> None:
    for key in SETTING_DEFAULTS:
        if key in values:
            v = values[key].strip().replace("%", "")
            set_setting(conn, key, v if v else "")


# ----------------------------------------------------------------- observed
def observed_contributions(conn: sqlite3.Connection) -> dict[tuple[str, int], float]:
    """Average monthly net inflow into each savings account (pot) over the complete calendar months
    the account covers. Months with no transfers count as zero; the current partial month is ignored."""
    import calendar

    out: dict[tuple[str, int], float] = {}
    flows = {(r["account_id"], r["m"]): r["net"] for r in conn.execute(
        """SELECT t.account_id, substr(t.date, 1, 7) AS m, SUM(t.amount) AS net
           FROM transactions t JOIN accounts a ON a.id = t.account_id
           WHERE a.kind = 'savings' AND t.type_code = 'POT'
           GROUP BY t.account_id, m""")}
    for a in reports.coverage(conn):
        if a["kind"] != "savings":
            continue
        first, last = date.fromisoformat(a["first"]), date.fromisoformat(a["last"])
        months = []
        y, m = (first.year, first.month) if first.day == 1 else ((first.year + 1, 1) if first.month == 12 else (first.year, first.month + 1))
        while date(y, m, calendar.monthrange(y, m)[1]) <= last:
            months.append(f"{y:04d}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        if not months:
            continue
        total = sum(flows.get((a["id"], mm), 0.0) for mm in months)
        out[("account", a["id"])] = round(total / len(months), 2)
    return out


def observed_rates(conn: sqlite3.Connection) -> dict[tuple[str, int], float]:
    """Annualised interest actually paid on savings accounts: interest ÷ balance, from the last 3 interest rows."""
    out: dict[tuple[str, int], float] = {}
    rows = conn.execute(
        """SELECT account_id, amount, balance_after FROM transactions
           WHERE type_code = 'INT' AND balance_after IS NOT NULL AND balance_after > 0
           ORDER BY date DESC"""
    ).fetchall()
    seen: dict[int, list[float]] = {}
    for r in rows:
        lst = seen.setdefault(r["account_id"], [])
        if len(lst) < 3:
            lst.append(r["amount"] / (r["balance_after"] - r["amount"]) * 12 * 100)
    for acct_id, rates in seen.items():
        out[("account", acct_id)] = round(sum(rates) / len(rates), 2)
    return out


def observed_overpayment(conn: sqlite3.Connection) -> float:
    rows = conn.execute(
        """SELECT substr(t.date, 1, 7) AS m, SUM(-t.amount) AS v FROM transactions t
           JOIN categories c ON c.id = t.category_id
           WHERE c.name = 'Mortgage overpayment' GROUP BY m"""
    ).fetchall()
    return round(statistics.median(r["v"] for r in rows), 2) if rows else 0.0


# ----------------------------------------------------------------- scenarios
def ensure_default_scenario(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM scenarios WHERE is_default = 1").fetchone()
    if row:
        return row["id"]
    with conn:
        cur = conn.execute(
            "INSERT INTO scenarios (name, is_default, created_at, notes) VALUES ('Base', 1, ?, ?)",
            (now_iso(), "Seeded from today's balances, observed contributions and the Settings defaults."),
        )
        return cur.lastrowid


def scenarios(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    ensure_default_scenario(conn)
    return conn.execute("SELECT * FROM scenarios ORDER BY is_default DESC, name").fetchall()


def clone_scenario(conn: sqlite3.Connection, scenario_id: int, name: str) -> int:
    src = conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
    with conn:
        cur = conn.execute(
            """INSERT INTO scenarios (name, is_default, horizon_years, inflation_pct, property_growth_pct,
                                      mortgage_rate_after_fix, mortgage_overpayment, contribution_growth_pct, notes, created_at,
                                      surplus_mode, surplus_override, surplus_target, category_savings)
               VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, src["horizon_years"], src["inflation_pct"], src["property_growth_pct"], src["mortgage_rate_after_fix"],
             src["mortgage_overpayment"], src["contribution_growth_pct"], f"Cloned from {src['name']}", now_iso(),
             src["surplus_mode"], src["surplus_override"], src["surplus_target"], src["category_savings"]),
        )
        new_id = cur.lastrowid
        conn.execute(
            """INSERT INTO scenario_items (scenario_id, ref_type, ref_id, annual_growth_pct, monthly_contribution, contribution_until)
               SELECT ?, ref_type, ref_id, annual_growth_pct, monthly_contribution, contribution_until
               FROM scenario_items WHERE scenario_id = ?""",
            (new_id, scenario_id),
        )
    return new_id


def delete_scenario(conn: sqlite3.Connection, scenario_id: int) -> bool:
    row = conn.execute("SELECT is_default FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
    if not row or row["is_default"]:
        return False
    with conn:
        conn.execute("DELETE FROM scenarios WHERE id = ?", (scenario_id,))
    return True


def save_levers(conn: sqlite3.Connection, scenario_id: int, surplus_mode: str, surplus_override: float | None,
                surplus_target: str | None, category_savings: dict[int, float]) -> None:
    with conn:
        conn.execute(
            "UPDATE scenarios SET surplus_mode = ?, surplus_override = ?, surplus_target = ?, category_savings = ? WHERE id = ?",
            (surplus_mode if surplus_mode in ("ignore", "save") else "ignore", surplus_override, surplus_target or None,
             json.dumps({str(k): v for k, v in category_savings.items() if v}), scenario_id),
        )


def save_assumptions(conn: sqlite3.Connection, scenario_id: int, globals_: dict, items: list[dict]) -> None:
    """items: [{ref_type, ref_id, annual_growth_pct|None, monthly_contribution|None, contribution_until|None}]"""
    with conn:
        conn.execute(
            """UPDATE scenarios SET name = COALESCE(?, name), horizon_years = ?, inflation_pct = ?, property_growth_pct = ?,
                      mortgage_rate_after_fix = ?, mortgage_overpayment = ?, contribution_growth_pct = ? WHERE id = ?""",
            (globals_.get("name"), globals_.get("horizon_years"), globals_.get("inflation_pct"), globals_.get("property_growth_pct"),
             globals_.get("mortgage_rate_after_fix"), globals_.get("mortgage_overpayment"),
             globals_.get("contribution_growth_pct") or 0.0, scenario_id),
        )
        for it in items:
            conn.execute(
                """INSERT INTO scenario_items (scenario_id, ref_type, ref_id, annual_growth_pct, monthly_contribution, contribution_until)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(scenario_id, ref_type, ref_id) DO UPDATE SET annual_growth_pct = excluded.annual_growth_pct,
                       monthly_contribution = excluded.monthly_contribution, contribution_until = excluded.contribution_until""",
                (scenario_id, it["ref_type"], it["ref_id"], it.get("annual_growth_pct"), it.get("monthly_contribution"),
                 it.get("contribution_until")),
            )


# ------------------------------------------------------------------- engine
@dataclass
class Item:
    key: str
    name: str
    kind: str
    balance: float
    rate: float                   # annual %
    contribution: float = 0.0     # per month, signed (+ in)
    until: date | None = None
    is_liability: bool = False
    rate_source: str = "default"
    contribution_source: str = "none"
    parent_key: str | None = None
    payment: float = 0.0          # mortgages/loans: monthly payment (interest + capital)
    overpayment: float = 0.0
    rate_after_fix: float | None = None
    fix_end: date | None = None


@dataclass
class Inputs:
    items: list[Item]
    start: date
    horizon_years: int
    inflation_pct: float
    property_growth_pct: float
    contribution_growth_pct: float = 0.0
    labels: dict = field(default_factory=dict)


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def run(inp: Inputs) -> dict:
    """Deterministic monthly projection. Returns per-month rows and milestones."""
    balances = {it.key: it.balance for it in inp.items}
    contrib_cum = 0.0
    rows = []
    payoff: dict[str, int] = {}
    months = inp.horizon_years * 12
    for m in range(0, months + 1):
        when = _add_months(inp.start, m)
        if m > 0:
            years = (m - 1) / 12
            for it in inp.items:
                b = balances[it.key]
                if it.kind == "mortgage" or it.kind == "loan":
                    if b <= 0:
                        continue
                    rate = it.rate
                    if it.fix_end and when > it.fix_end and it.rate_after_fix is not None:
                        rate = it.rate_after_fix
                    interest = b * rate / 100 / 12
                    capital = it.payment + it.overpayment - interest
                    if capital <= 0:
                        capital = 0.0
                    b = max(0.0, b - capital)
                    if b == 0.0 and it.key not in payoff:
                        payoff[it.key] = m
                    balances[it.key] = b
                    continue
                rate = inp.property_growth_pct if it.kind == "property" else it.rate
                if it.kind == "property":
                    b *= (1 + rate / 100) ** (1 / 12)
                elif b > 0:
                    b += b * rate / 100 / 12   # a negative balance (cash being drawn down) earns nothing
                c = it.contribution * (1 + inp.contribution_growth_pct / 100) ** years
                if it.until and when > it.until:
                    c = 0.0
                if c:
                    b += c
                    contrib_cum += c
                balances[it.key] = b
        assets = sum(v for it in inp.items if not it.is_liability for v in [balances[it.key]])
        liabilities = sum(v for it in inp.items if it.is_liability for v in [balances[it.key]])
        nw = assets - liabilities
        by_class: dict[str, float] = {}
        for it in inp.items:
            cls = ASSET_CLASS.get(it.kind, "Other")
            by_class[cls] = by_class.get(cls, 0.0) + (-balances[it.key] if it.is_liability else balances[it.key])
        property_value = sum(balances[it.key] for it in inp.items if it.kind == "property")
        mortgage = sum(balances[it.key] for it in inp.items if it.kind == "mortgage")
        rows.append({
            "month": m, "date": when.isoformat(), "net_worth": nw,
            "net_worth_real": nw / (1 + inp.inflation_pct / 100) ** (m / 12),
            "assets": assets, "liabilities": liabilities, "contributions": contrib_cum,
            "growth": nw - inp_net_worth(inp) - contrib_cum,
            "by_class": by_class, "balances": dict(balances),
            "property": property_value, "mortgage": mortgage, "equity": property_value - mortgage,
            "liquid": sum(balances[it.key] for it in inp.items if it.kind in ("current", "savings", "cash", "cash_isa", "stocks", "stocks_isa")),
        })
    milestones = [rows[y * 12] for y in MILESTONE_YEARS if y * 12 <= months]
    return {"rows": rows, "milestones": milestones, "payoff": payoff, "start_net_worth": inp_net_worth(inp),
            "classes": sorted({c for r in rows for c in r["by_class"]}, key=lambda c: CLASS_ORDER.index(c) if c in CLASS_ORDER else 99)}


def inp_net_worth(inp: Inputs) -> float:
    return sum(-it.balance if it.is_liability else it.balance for it in inp.items)


def band_series(result: dict) -> dict[str, list[float]]:
    """One series per asset class in CLASS_ORDER; summed month by month they equal net worth exactly."""
    return {c: [round(r["by_class"].get(c, 0.0), 2) for r in result["rows"]] for c in result["classes"]}


def yearly_rows(result: dict) -> list[dict]:
    """The chart's table twin: one row per anniversary (year 0 is today) with each band and the net worth lines."""
    return [{**r, "year": r["month"] // 12} for r in result["rows"] if r["month"] % 12 == 0]


def swatches(classes: list[str]) -> dict[str, str]:
    """Band -> token name: the ordinal blue ramp bottom to top; a fifth or later band falls back to the neutral slot."""
    ramps = ["ramp-1", "ramp-2", "ramp-3", "ramp-4"]
    return {c: (ramps[i] if i < len(ramps) else "chart-other") for i, c in enumerate(classes)}


def compact_money(value: float | None) -> str:
    """Tile and tick format: whole pounds to £99,999, then £280.4k, then £1.2M. True minus."""
    if value is None:
        return "—"
    sign = "−" if value < 0 else ""
    v = abs(value)
    if v >= 1_000_000:
        return f"{sign}£{v / 1_000_000:.1f}".replace(".0", "") + "M"
    if v >= 100_000:
        return f"{sign}£{v / 1000:.1f}".replace(".0", "") + "k"
    return f"{sign}£{v:,.0f}"


def plan_summary(levers: dict, cf: dict) -> dict:
    """The sanity check beside the tiles: planned saving (contributions plus levers) against average net."""
    planned = levers["explicit_contributions"] + levers["extra_monthly"]
    net = cf["net"] if cf["months"] else None
    return {"planned": planned, "contributions": levers["explicit_contributions"], "from_levers": levers["extra_monthly"],
            "average_net": net, "gap": (planned - net) if net is not None else None,
            "surplus": levers["derived_surplus"] if cf["months"] else None,
            "exceeds": net is not None and planned > net + 0.5}


# ------------------------------------------------------------- cash flow
def cash_flow(conn: sqlite3.Connection) -> dict:
    """Average monthly income, outgoings and spend per category over the last three covered months."""
    months, _cov = reports.trend_months(conn, 3)
    if not months:
        return {"months": [], "income": 0.0, "outgoings": 0.0, "net": 0.0, "categories": []}
    table = reports.trend_table(conn, months)
    cats = [{"id": c["id"], "name": c["name"], "avg": c["avg"], "discretionary": c["discretionary"], "fixed_share": c["fixed_share"]}
            for c in table["categories"] if c["avg"] > 0 and c["id"] is not None]
    return {"months": months, "income": table["totals"]["avg_income"], "outgoings": table["totals"]["avg_outgoings"],
            "net": table["totals"]["avg_net"], "categories": cats}


def levers_summary(cf: dict, category_savings: dict, explicit_contributions: float, surplus_mode: str,
                   surplus_override: float | None) -> dict:
    """What the sliders and the surplus setting add per month."""
    rows = []
    saved_total = 0.0
    for c in cf["categories"]:
        pct = float(category_savings.get(str(c["id"]), category_savings.get(c["id"], 0)) or 0)
        saved = c["avg"] * pct / 100
        saved_total += saved
        rows.append({**c, "pct": pct, "saved": saved, "new_spend": c["avg"] - saved})
    derived_surplus = cf["net"] - explicit_contributions
    surplus = surplus_override if surplus_override is not None else derived_surplus
    surplus_used = surplus if surplus_mode == "save" else 0.0
    return {"rows": rows, "saved_total": saved_total, "derived_surplus": derived_surplus, "surplus": surplus,
            "surplus_used": surplus_used, "extra_monthly": saved_total + surplus_used,
            "explicit_contributions": explicit_contributions}


# ------------------------------------------------------------ assemble inputs
def build_inputs(conn: sqlite3.Connection, scenario_id: int, start: date | None = None,
                 live: dict | None = None) -> tuple[Inputs, dict]:
    """Combine balances, scenario overrides, observed figures and settings into engine inputs.
    Also returns per-item metadata for the assumptions table.

    `live` can override the levers without saving: {category_savings, surplus_mode, surplus_override, surplus_target}."""
    sc = conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
    overrides = {(r["ref_type"], r["ref_id"]): r for r in conn.execute(
        "SELECT * FROM scenario_items WHERE scenario_id = ?", (scenario_id,))}
    obs_contrib = observed_contributions(conn)
    obs_rates = observed_rates(conn)
    items: list[Item] = []
    meta: dict = {}

    def resolve(ref, kind, name, balance, is_liability=False, **extra):
        o = overrides.get(ref)
        obs_rate = obs_rates.get(ref)
        if o is not None and o["annual_growth_pct"] is not None:
            rate, rate_src = o["annual_growth_pct"], "override"
        elif obs_rate is not None:
            rate, rate_src = obs_rate, "observed"
        else:
            rate, rate_src = setting(conn, f"proj.rate.{kind}") if f"proj.rate.{kind}" in SETTING_DEFAULTS else 0.0, "default"
        obs_c = obs_contrib.get(ref)
        if o is not None and o["monthly_contribution"] is not None:
            contrib, c_src = o["monthly_contribution"], "override"
        elif obs_c is not None:
            contrib, c_src = obs_c, "observed"
        else:
            contrib, c_src = 0.0, "none"
        until = date.fromisoformat(o["contribution_until"]) if o is not None and o["contribution_until"] else None
        key = f"{ref[0]}:{ref[1]}"
        it = Item(key=key, name=name, kind=kind, balance=balance, rate=rate, contribution=contrib, until=until,
                  is_liability=is_liability, rate_source=rate_src, contribution_source=c_src, **extra)
        items.append(it)
        meta[key] = {"ref_type": ref[0], "ref_id": ref[1], "observed_rate": obs_rate, "observed_contribution": obs_c,
                     "default_rate": setting(conn, f"proj.rate.{kind}") if f"proj.rate.{kind}" in SETTING_DEFAULTS else 0.0,
                     "override_rate": o["annual_growth_pct"] if o is not None else None,
                     "override_contribution": o["monthly_contribution"] if o is not None else None,
                     "until": o["contribution_until"] if o is not None else None}
        return it

    for a in reports.account_balances(conn):
        if a["balance"] is None:
            continue
        resolve(("account", a["id"]), a["kind"], a["name"], a["balance"])
    overpay = sc["mortgage_overpayment"] if sc["mortgage_overpayment"] is not None else observed_overpayment(conn)
    for h in reports.holdings(conn):
        if h["kind"] == "mortgage":
            it = resolve(("holding", h["id"]), "mortgage", h["name"], h["value"], is_liability=True,
                         payment=h["monthly_payment"] or 0.0, overpayment=overpay,
                         fix_end=date.fromisoformat(h["fix_end"]) if h["fix_end"] else None,
                         rate_after_fix=sc["mortgage_rate_after_fix"])
            it.rate, it.rate_source = (h["rate"] or 0.0), "holding"
            meta[it.key]["parent"] = h["parent_id"]
        elif h["kind"] in ("loan", "other_liability"):
            it = resolve(("holding", h["id"]), h["kind"], h["name"], h["value"], is_liability=True,
                         payment=h["monthly_payment"] or 0.0)
            if h["rate"] is not None:
                it.rate, it.rate_source = h["rate"], "holding"
        else:
            it = resolve(("holding", h["id"]), h["kind"], h["name"], h["value"])
            if h["kind"] in ("cash", "cash_isa") and h["rate"] is not None:
                it.rate, it.rate_source = h["rate"], "holding"
    # --- spending levers and surplus
    live = live or {}
    category_savings = live.get("category_savings")
    if category_savings is None:
        category_savings = json.loads(sc["category_savings"] or "{}")
    surplus_mode = live.get("surplus_mode") or sc["surplus_mode"] or "ignore"
    surplus_override = live["surplus_override"] if "surplus_override" in live else sc["surplus_override"]
    surplus_target = live.get("surplus_target") if "surplus_target" in live else sc["surplus_target"]
    cf = cash_flow(conn)
    explicit = sum(it.contribution for it in items if not it.is_liability)
    levers = levers_summary(cf, category_savings, explicit, surplus_mode, surplus_override)
    if levers["extra_monthly"]:
        target = next((it for it in items if it.key == surplus_target), None)
        if target is None:
            target = Item(key="surplus", name="Unallocated cash (surplus and savings)", kind="cash", balance=0.0,
                          rate=setting(conn, "proj.rate.cash"), rate_source="default", contribution_source="levers")
            items.append(target)
        target.contribution += levers["extra_monthly"]
    inp = Inputs(
        items=items, start=start or date.today().replace(day=1),
        horizon_years=int(sc["horizon_years"] or setting(conn, "proj.horizon_years")),
        inflation_pct=sc["inflation_pct"] if sc["inflation_pct"] is not None else setting(conn, "proj.inflation"),
        property_growth_pct=sc["property_growth_pct"] if sc["property_growth_pct"] is not None else setting(conn, "proj.rate.property"),
        contribution_growth_pct=sc["contribution_growth_pct"] or 0.0,
    )
    meta["_scenario"] = dict(sc)
    meta["_observed_overpayment"] = observed_overpayment(conn)
    meta["_cash_flow"] = cf
    meta["_levers"] = {**levers, "surplus_mode": surplus_mode, "surplus_override": surplus_override, "surplus_target": surplus_target,
                       "category_savings": category_savings}
    return inp, meta
