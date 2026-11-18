"""Renewals: contracts and dates the household wants to act on before they roll over.

A renewal is due when its reminder (end date minus lead time) starts before roughly the next month close, so
nothing slips between two closes. The mortgage fix is mirrored from its Net worth holding, never entered twice."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import reports
from .db import now_iso

KINDS = ("insurance", "energy", "broadband", "mobile", "car", "mortgage", "other")
KIND_LABELS = {"insurance": "Insurance", "energy": "Energy", "broadband": "Broadband", "mobile": "Mobile",
               "car": "Car date", "mortgage": "Mortgage fix", "other": "Other"}
PERIODS = ("month", "year", "once")
OUTCOMES = ("stayed", "switched", "haggled", "done")
DEFAULT_LEAD = {"mortgage": 183}
LEAD_DAYS = 91
LOOKAHEAD_DAYS = 35     # about one month-close cycle
SOON_DAYS = 183
GROUP_ORDER = {"due": 0, "soon": 1, "later": 2, "archived": 3}
TYPE_PAID = {"DD": "direct debit", "SO": "standing order"}
PERIOD_PAID = {"month": "paid monthly", "year": "paid yearly", "once": "one payment"}
FIELDS = ("name", "kind", "provider", "owner", "ends_on", "lead_days", "price", "price_period", "payee_key", "notes")


def annual(price: float | None, period: str | None) -> float | None:
    if price is None:
        return None
    return price * 12 if period == "month" else price


# ------------------------------------------------------------------ mortgage
def sync_mortgages(conn: sqlite3.Connection) -> None:
    """One row per mortgage holding with a fix end, kept in step with it; rows whose holding is gone or has no fix
    end are archived (with their history), never deleted."""
    with conn:
        for h in conn.execute("SELECT * FROM holdings WHERE kind = 'mortgage' AND fix_end IS NOT NULL").fetchall():
            values = (h["provider"], h["owner"], h["fix_end"], h["monthly_payment"])
            if conn.execute("SELECT 1 FROM renewals WHERE holding_id = ?", (h["id"],)).fetchone():
                conn.execute("""UPDATE renewals SET provider = ?, owner = ?, ends_on = ?, price = ?, price_period = 'month',
                                archived_at = NULL WHERE holding_id = ?""", (*values, h["id"]))
            else:
                conn.execute("""INSERT INTO renewals (name, kind, provider, owner, ends_on, price, price_period, holding_id,
                                created_at) VALUES ('Mortgage fix', 'mortgage', ?, ?, ?, ?, 'month', ?, ?)""",
                             (*values, h["id"], now_iso()))
        conn.execute("""UPDATE renewals SET archived_at = ? WHERE kind = 'mortgage' AND archived_at IS NULL AND (
                            holding_id IS NULL OR holding_id NOT IN (
                                SELECT id FROM holdings WHERE kind = 'mortgage' AND fix_end IS NOT NULL))""", (now_iso(),))


# ------------------------------------------------------------------ register
def _payments(conn: sqlite3.Connection) -> dict[str, dict]:
    """Latest recurring payment per merchant key."""
    out: dict[str, dict] = {}
    for r in reports.recurring(conn):
        if r["merchant"] not in out or r["last_date"] > out[r["merchant"]]["last_date"]:
            out[r["merchant"]] = r
    return out


def _last(conn: sqlite3.Connection, renewal_id: int) -> dict | None:
    e = conn.execute("SELECT * FROM renewal_events WHERE renewal_id = ? ORDER BY id DESC LIMIT 1", (renewal_id,)).fetchone()
    if e is None:
        return None
    saved = saving(dict(e))
    old, new = annual(e["old_price"], e["old_period"]), annual(e["new_price"], e["new_period"])
    rise = None
    if e["outcome"] == "stayed" and old and new is not None and new > old:
        rise = round((new - old) / old * 100)
    return {"outcome": e["outcome"], "saved": saved if saved and saved > 0 else None, "rise_pct": rise}


def _row(conn: sqlite3.Connection, r: sqlite3.Row, today: date, payments: dict[str, dict]) -> dict:
    d = dict(r)
    d["kind_label"] = KIND_LABELS[d["kind"]]
    d["lead_days"] = d["lead_days"] or DEFAULT_LEAD.get(d["kind"], LEAD_DAYS)
    d["archived"] = d["archived_at"] is not None
    ends = date.fromisoformat(d["ends_on"]) if d["ends_on"] else None
    d["days_left"] = (ends - today).days if ends else None
    d["reminder_on"] = (ends - timedelta(days=d["lead_days"])).isoformat() if ends else None
    if d["archived"] or ends is None:
        d["group"] = "archived"
    elif ends - timedelta(days=d["lead_days"]) <= today + timedelta(days=LOOKAHEAD_DAYS):
        d["group"] = "due"
    elif d["days_left"] <= SOON_DAYS:
        d["group"] = "soon"
    else:
        d["group"] = "later"
    pay = payments.get(d["payee_key"]) if d["payee_key"] else None
    d["how_paid"] = TYPE_PAID.get(pay["type_code"], "card") if pay else PERIOD_PAID[d["price_period"]]
    d["latest_payment"] = None
    if pay and d["price_period"] == "month" and d["price"] and abs(pay["last"] - d["price"]) > 0.01 * d["price"]:
        d["latest_payment"] = pay["last"]
    d["last"] = _last(conn, d["id"])
    return d


def register(conn: sqlite3.Connection, today: date) -> list[dict]:
    sync_mortgages(conn)
    payments = _payments(conn)
    rows = [_row(conn, r, today, payments) for r in conn.execute("SELECT * FROM renewals").fetchall()]
    rows.sort(key=lambda d: (GROUP_ORDER[d["group"]], d["ends_on"] or "9999", d["name"].lower()))
    return rows


def due(conn: sqlite3.Connection, today: date) -> list[dict]:
    return [r for r in register(conn, today) if r["group"] == "due"]


def get(conn: sqlite3.Connection, renewal_id: int, today: date) -> dict | None:
    return next((r for r in register(conn, today) if r["id"] == renewal_id), None)


def save_renewal(conn: sqlite3.Connection, data: dict, renewal_id: int | None = None) -> int:
    """Create or update a renewal. Input is trusted (the route validates). A mortgage row keeps the fields it mirrors
    from its holding: only its lead time and notes change here."""
    with conn:
        if renewal_id is None:
            cur = conn.execute(f"INSERT INTO renewals ({', '.join(FIELDS)}, created_at) VALUES ({', '.join('?' * len(FIELDS))}, ?)",
                               [*(data.get(f) for f in FIELDS), now_iso()])
            return cur.lastrowid
        linked = conn.execute("SELECT holding_id FROM renewals WHERE id = ?", (renewal_id,)).fetchone()
        fields = ("lead_days", "notes") if linked and linked["holding_id"] else FIELDS
        conn.execute(f"UPDATE renewals SET {', '.join(f + ' = ?' for f in fields)} WHERE id = ?",
                     [*(data.get(f) for f in fields), renewal_id])
        return renewal_id


def delete_renewal(conn: sqlite3.Connection, renewal_id: int) -> bool:
    """Delete a renewal and its history. A row mirroring a mortgage holding cannot be deleted here."""
    row = conn.execute("SELECT holding_id FROM renewals WHERE id = ?", (renewal_id,)).fetchone()
    if row is None or row["holding_id"] is not None:
        return False
    with conn:
        conn.execute("DELETE FROM renewals WHERE id = ?", (renewal_id,))
    return True


# ------------------------------------------------------------------ events
def saving(event: dict) -> float | None:
    """The renewal quote minus what was chosen, both a year's worth; None without a quote."""
    if event.get("quote") is None or event.get("new_price") is None:
        return None
    return round(annual(event["quote"], event.get("quote_period")) - annual(event["new_price"], event.get("new_period")), 2)


EVENT_FIELDS = ("outcome", "quote", "quote_period", "other_quote", "other_period", "other_provider",
                "new_provider", "new_price", "new_period", "new_ends_on", "new_rate", "new_payment")


def record(conn: sqlite3.Connection, renewal_id: int, data: dict, today: date) -> int:
    """Record a renewal: keep the term it replaces on the event, then move the renewal (or, for the mortgage, its
    holding: fix end, rate, payment, lender) onto the new term."""
    sync_mortgages(conn)
    r = conn.execute("SELECT * FROM renewals WHERE id = ?", (renewal_id,)).fetchone()
    if r is None or r["archived_at"] is not None:
        raise ValueError("this renewal is archived or gone")
    h = conn.execute("SELECT * FROM holdings WHERE id = ?", (r["holding_id"],)).fetchone() if r["holding_id"] else None
    with conn:
        cur = conn.execute(
            f"""INSERT INTO renewal_events (renewal_id, recorded_on, old_provider, old_price, old_period, old_ends_on, old_rate,
                    {', '.join(EVENT_FIELDS)}) VALUES (?, ?, ?, ?, ?, ?, ?, {', '.join('?' * len(EVENT_FIELDS))})""",
            [renewal_id, today.isoformat(), r["provider"], r["price"], r["price_period"], r["ends_on"],
             h["rate"] if h else None, *(data.get(f) for f in EVENT_FIELDS)])
        if h:
            payment = data.get("new_payment") if data.get("new_payment") is not None else data.get("new_price")
            conn.execute("UPDATE holdings SET fix_end = ?, rate = COALESCE(?, rate), monthly_payment = COALESCE(?, monthly_payment), "
                         "provider = ? WHERE id = ?",
                         (data["new_ends_on"], data.get("new_rate"), payment, data.get("new_provider"), h["id"]))
        else:
            conn.execute("UPDATE renewals SET provider = ?, price = ?, price_period = ?, ends_on = ? WHERE id = ?",
                         (data.get("new_provider"), data.get("new_price"), data.get("new_period") or r["price_period"],
                          data["new_ends_on"], renewal_id))
    sync_mortgages(conn)
    return cur.lastrowid


def history(conn: sqlite3.Connection, renewal_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM renewal_events WHERE renewal_id = ? ORDER BY id DESC", (renewal_id,)).fetchall()
    return [{**dict(e), "saved": saving(dict(e))} for e in rows]


def delete_latest_event(conn: sqlite3.Connection, renewal_id: int) -> bool:
    """Undo the newest renewal: put back the term it replaced (on the holding, for the mortgage), then drop it."""
    e = conn.execute("SELECT * FROM renewal_events WHERE renewal_id = ? ORDER BY id DESC LIMIT 1", (renewal_id,)).fetchone()
    if e is None:
        return False
    r = conn.execute("SELECT holding_id FROM renewals WHERE id = ?", (renewal_id,)).fetchone()
    with conn:
        if r["holding_id"]:
            conn.execute("UPDATE holdings SET fix_end = ?, rate = ?, monthly_payment = ?, provider = ? WHERE id = ?",
                         (e["old_ends_on"], e["old_rate"], e["old_price"], e["old_provider"], r["holding_id"]))
        else:
            conn.execute("UPDATE renewals SET provider = ?, price = ?, price_period = ?, ends_on = ? WHERE id = ?",
                         (e["old_provider"], e["old_price"], e["old_period"], e["old_ends_on"], renewal_id))
        conn.execute("DELETE FROM renewal_events WHERE id = ?", (e["id"],))
    sync_mortgages(conn)
    return True


def saved_in_year(conn: sqlite3.Connection, year: int) -> float:
    rows = conn.execute("SELECT * FROM renewal_events WHERE substr(recorded_on, 1, 4) = ?", (str(year),)).fetchall()
    return round(sum(s for s in (saving(dict(e)) for e in rows) if s is not None), 2)


# ------------------------------------------------------------------ suggestions
SUGGEST_TOPS = {"Housing", "Utilities", "Transport", "Pets"}
SUGGEST_SKIP = {"Mortgage", "Mortgage overpayment", "Council tax", "Water"}   # not switched or renewed


def _looks_like_contract(category: str | None) -> bool:
    if not category:
        return False
    top, _, leaf = category.partition(" / ")
    return (top in SUGGEST_TOPS and (leaf or top) not in SUGGEST_SKIP) or "insurance" in category.lower()


def display_name(merchant: str) -> str:
    """'HOMESAFE INSURANCE' -> 'Homesafe Insurance', leaving codes and acronyms ('DVLA-EX24 DMO') as printed."""
    return " ".join(w.capitalize() if w.isalpha() and len(w) > 3 else w for w in merchant.split())


def guess_kind(category: str) -> str:
    c = category.lower()
    if "insurance" in c:
        return "insurance"
    if "energy" in c:
        return "energy"
    if "broadband" in c:
        return "broadband"
    return "other"


def suggestions(conn: sqlite3.Connection) -> list[dict]:
    """Direct debits and standing orders that look like contracts and aren't on the list or dismissed."""
    taken = {r[0] for r in conn.execute("SELECT payee_key FROM renewals WHERE payee_key IS NOT NULL")}
    taken |= {r[0] for r in conn.execute("SELECT payee_key FROM dismissed_renewal_suggestions")}
    out = []
    for r in reports.recurring(conn):
        if r["fixed"] and r["merchant"] not in taken and _looks_like_contract(r["category"]):
            taken.add(r["merchant"])
            out.append({"payee_key": r["merchant"], "type_code": r["type_code"], "category": r["category"],
                        "months": r["months"], "per_month": r["per_month"], "last": r["last"],
                        "name": r["category"].split(" / ")[-1], "provider": display_name(r["merchant"]),
                        "kind": guess_kind(r["category"])})
    return out


def dismiss_suggestion(conn: sqlite3.Connection, payee_key: str) -> None:
    with conn:
        conn.execute("INSERT OR IGNORE INTO dismissed_renewal_suggestions (payee_key, dismissed_at) VALUES (?, ?)",
                     (payee_key, now_iso()))


def payee_choices(conn: sqlite3.Connection) -> list[dict]:
    """Every recurring payment, for linking a renewal to the payments it shows up as."""
    seen: dict[str, dict] = {}
    for r in reports.recurring(conn):
        seen.setdefault(r["merchant"], {"merchant": r["merchant"], "category": r["category"], "per_month": r["per_month"]})
    return sorted(seen.values(), key=lambda p: p["merchant"])
