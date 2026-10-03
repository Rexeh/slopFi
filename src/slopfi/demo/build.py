"""`slopfi demo`: write the Rivera household's statements, sync them into a fresh database and dress it up.

The result is a database every page has something to show for: a closed year, one open month with work left
on the Review checklist, holdings with a valuation history, targets, a one-off and a saved projection scenario.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .. import categorise, db, household as household_mod, overview, projection, reports, review, rules_io
from .. import sync as sync_mod
from . import household, writers

DEFAULT_DIR = Path("demo")
DB_NAME = "slopfi-demo.db"
SERVE_HINT = "SLOPFI_DB={db} uv run slopfi serve"

# The demo's own rules: most merchants are covered, a handful are left for the Review flow on purpose
# (HILLTOP FARM SHOP, THE OLD BELL, SQ *COPPER KETTLE, BARBER & CO, WHEELERS GARDEN CENTRE, WOODLAND LODGES,
# SCHOOLWEAR, the holiday cafés and Sam's market stall).
DEMO_RULES: list[dict] = [
    {"pattern": "ACME ANALYTICS", "match_type": "contains", "category": "Income/Salary", "priority": 10},
    {"pattern": "SCHOOLS TRUST", "match_type": "contains", "category": "Income/Salary", "priority": 10},
    {"pattern": "REFUND", "match_type": "contains", "category": "Income/Refunds", "priority": 5, "amount_min": 0},
    {"pattern": "INTEREST FOR", "match_type": "prefix", "category": "Income/Interest received", "priority": 10,
     "type_code": "INT"},
    {"pattern": "TRANSFER", "match_type": "contains", "category": "Transfers/Savings", "priority": 10, "type_code": "POT"},
    {"pattern": "EXAMPLE SAVINGS", "match_type": "contains", "category": "Transfers/Savings", "priority": 10},
    {"pattern": "EXAMPLE INVEST", "match_type": "contains", "category": "Transfers/Investments", "priority": 10},
    {"pattern": "AMERICAN EXPRESS", "match_type": "contains", "category": "Transfers/Credit card payment",
     "priority": 10, "type_code": "DD"},
    {"pattern": "PAYMENT RECEIVED", "match_type": "prefix", "category": "Transfers/Credit card payment", "priority": 10},
    {"pattern": "RIVERA", "match_type": "contains", "category": "Transfers/Between own accounts", "priority": 20},
    {"pattern": "EXAMPLE HOME LOANS / MTG", "match_type": "prefix", "category": "Housing/Mortgage", "priority": 10,
     "type_code": "DD"},
    {"pattern": "OVERPAYMENT", "match_type": "contains", "category": "Housing/Mortgage overpayment", "priority": 10},
    {"pattern": "EXAMPLETOWN COUNCIL", "match_type": "contains", "category": "Housing/Council tax"},
    {"pattern": "HOMESAFE", "match_type": "contains", "category": "Housing/Home insurance"},
    {"pattern": "B&Q", "match_type": "prefix", "category": "Housing/Maintenance & DIY"},
    {"pattern": "BRIGHTSPARK ENERGY", "match_type": "contains", "category": "Utilities/Energy"},
    {"pattern": "EXAMPLE WATER", "match_type": "contains", "category": "Utilities/Water"},
    {"pattern": "FIBRELINE", "match_type": "contains", "category": "Utilities/Broadband & phone"},
    {"pattern": "SKYLINE MOBILE", "match_type": "contains", "category": "Utilities/Broadband & phone"},
    {"pattern": r"NETFLIX|DISNEY PLUS", "match_type": "regex", "category": "Utilities/TV & streaming"},
    {"pattern": "SPOTIFY", "match_type": "contains", "category": "Leisure/Subscriptions"},
    {"pattern": "AMAZON PRIME", "match_type": "contains", "category": "Leisure/Subscriptions", "priority": 40},
    {"pattern": "APPLE.COM/BILL", "match_type": "contains", "category": "Leisure/Subscriptions"},
    {"pattern": r"\b(TESCO|SAINSBURYS|ALDI|LIDL|WAITROSE|OCADO)\b", "match_type": "regex",
     "category": "Groceries/Supermarket"},
    {"pattern": "CO-OP", "match_type": "contains", "category": "Groceries/Local shop"},
    {"pattern": "COSTA COFFEE", "match_type": "contains", "category": "Eating out/Restaurants & cafes"},
    {"pattern": "PRET A MANGER", "match_type": "contains", "category": "Eating out/Restaurants & cafes"},
    {"pattern": "GREGGS", "match_type": "contains", "category": "Eating out/Restaurants & cafes"},
    {"pattern": "WAGAMAMA", "match_type": "contains", "category": "Eating out/Restaurants & cafes"},
    {"pattern": "DELIVEROO", "match_type": "contains", "category": "Eating out/Takeaway & delivery"},
    {"pattern": r"^(ESSO|SHELL) ", "match_type": "regex", "category": "Transport/Fuel"},
    {"pattern": "MOTORCARE", "match_type": "contains", "category": "Transport/Car maintenance"},
    {"pattern": "MOTORWISE", "match_type": "contains", "category": "Transport/Car insurance & tax"},
    {"pattern": "DVLA", "match_type": "prefix", "category": "Transport/Car insurance & tax", "type_code": "DD"},
    {"pattern": "TRAINLINE", "match_type": "contains", "category": "Transport/Public transport"},
    {"pattern": "LITTLE ACORNS", "match_type": "contains", "category": "Children/Childcare"},
    {"pattern": "SWIM SCHOOL", "match_type": "contains", "category": "Children/Classes & activities"},
    {"pattern": "SMYTHS", "match_type": "prefix", "category": "Children/Toys & books"},
    {"pattern": "PAWSURE", "match_type": "contains", "category": "Pets/Pet insurance"},
    {"pattern": "PETS AT HOME", "match_type": "contains", "category": "Pets/Pet food & supplies"},
    {"pattern": "EXAMPLETOWN VETS", "match_type": "contains", "category": "Pets/Vet"},
    {"pattern": "AMAZON.CO.UK", "match_type": "contains", "category": "Shopping/Online", "priority": 60},
    {"pattern": "JOHN LEWIS", "match_type": "contains", "category": "Shopping/Household"},
    {"pattern": r"^(NEXT RETAIL|UNIQLO)", "match_type": "regex", "category": "Shopping/Clothing"},
    {"pattern": "WATERSTONES", "match_type": "contains", "category": "Shopping/Gifts"},
    {"pattern": "BOOTS", "match_type": "prefix", "category": "Health & personal care/Pharmacy"},
    {"pattern": "FLEXFIT", "match_type": "contains", "category": "Health & personal care/Fitness"},
    {"pattern": r"EASYJET|HOTEL MIRAMAR|MERCADONA", "match_type": "regex", "category": "Leisure/Holidays"},
    {"pattern": "NON-STERLING", "match_type": "contains", "category": "Bank charges/FX fees", "priority": 5},
    {"pattern": "CASH", "match_type": "prefix", "category": "Cash/Cash withdrawal", "type_code": "ATM"},
]

TARGETS = {"Groceries": 720.0, "Eating out": 300.0, "Shopping": 220.0}

SOURCES_TOML = """\
# The Rivera household (fictional): written by `slopfi demo`. Safe to delete and rebuild.
# Run it with: SLOPFI_DB=demo/slopfi-demo.db SLOPFI_SOURCES=demo/sources.toml uv run slopfi serve
rules_file = "rules.json"
{sources}"""


@dataclass
class BuildResult:
    out: Path
    db_path: Path
    files: list[Path]
    transactions: int = 0
    uncategorised: int = 0
    rules: int = 0
    seconds: float = 0.0
    months: list[str] = field(default_factory=list)


def _sources_toml(hh: household.Household) -> str:
    blocks = []
    for acct in hh.accounts.values():
        note = {"monzo_pdf": "# One Monzo PDF carries the account and both pots; each pot becomes a savings account.\n",
                "monzo_csv": "# A CSV export does not say which account it is from: this entry does.\n"}.get(acct.fmt, "")
        blocks.append(f'\n{note}[[source]]\npath = "statements/{acct.folder}"\naccount = "{acct.name}"\n'
                      f'owner = "{acct.owner}"\nkind = "{acct.kind}"\n')
    return SOURCES_TOML.format(sources="".join(blocks))


def _clean(out: Path) -> None:
    """Remove only what a previous build wrote."""
    shutil.rmtree(out / "statements", ignore_errors=True)
    for name in ("sources.toml", "rules.json"):
        (out / name).unlink(missing_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        (out / f"{DB_NAME}{suffix}").unlink(missing_ok=True)


def _month_end(y: int, m: int) -> date:
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return nxt - timedelta(days=1)


def _account_id(conn, name: str) -> int:
    return conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()["id"]


def _holdings(conn, hh: household.Household) -> dict[str, int]:
    now = hh.end.isoformat()
    y, m = hh.months[-4]
    then = _month_end(y, m).isoformat()
    ids: dict[str, int] = {}
    house = {"name": "1 Example Street", "kind": "property", "owner": "joint", "provider": None, "notes": None}
    mortgage = {"provider": "Example Home Loans", "rate": 4.29, "monthly_payment": 1145.0,
                "fix_end": f"{hh.end.year + 2}-03-31", "term_end": f"{hh.end.year + 25}-06-30"}
    ids["house"] = reports.save_property(conn, {**house, "value": 380000.0, "valued_at": then},
                                         {**mortgage, "value": 214150.0})
    reports.save_property(conn, {**house, "value": 385000.0, "valued_at": now}, {**mortgage, "value": 212390.0},
                          ids["house"])
    for key, data, before, after in (
        ("cash_isa", {"name": "Sam's cash ISA", "kind": "cash_isa", "owner": "sam", "provider": "Example Savings",
                      "rate": 4.4}, 12700.0, 14260.0),
        ("stocks_isa", {"name": "Alex's stocks and shares ISA", "kind": "stocks_isa", "owner": "alex",
                        "provider": "Example Invest"}, 22150.0, 23780.0),
        ("pension", {"name": "Acme workplace pension", "kind": "pension", "owner": "alex",
                     "provider": "Example Pensions"}, 58900.0, 61350.0),
    ):
        hid = reports.save_holding(conn, {**data, "value": before, "valued_at": then})
        reports.save_holding(conn, {**data, "value": after, "valued_at": now}, hid)
        ids[key] = hid
    return ids


def _snapshot(conn, hh: household.Household) -> str:
    """One balance snapshot about three months back for the Monzo accounts, which have no statement balance then
    (one PDF for the year; the CSV has no balances at all), so Net worth over time has a second point."""
    y, m = hh.months[-4]
    day = _month_end(y, m)
    alex, sam = hh.accounts["alex-monzo"], hh.accounts["sam-monzo"]
    values = {alex.name: alex.balance_on(day), sam.name: sam.balance_on(day)}
    for pot in alex.pots:
        values[f"Monzo pot: {pot.name}"] = pot.opening + sum(t.pence for t in pot.txns if t.day <= day)
    for name, pence in values.items():
        reports.add_balance_snapshot(conn, _account_id(conn, name), day.isoformat(), pence / 100)
    return day.isoformat()


def _scenario(conn, holdings: dict[str, int]) -> int:
    base = projection.ensure_default_scenario(conn)
    projection.save_assumptions(conn, base, {"name": None}, [
        {"ref_type": "holding", "ref_id": holdings["stocks_isa"], "monthly_contribution": 300.0},
        {"ref_type": "holding", "ref_id": holdings["cash_isa"], "monthly_contribution": 500.0},
        {"ref_type": "holding", "ref_id": holdings["pension"], "monthly_contribution": 620.0},
    ])
    plan = projection.clone_scenario(conn, base, "Plan")
    levers = {db.category_id_by_path(conn, "Eating out"): 20.0, db.category_id_by_path(conn, "Shopping"): 15.0}
    projection.save_levers(conn, plan, "ignore", None, f"holding:{holdings['stocks_isa']}", levers)
    return plan


def build(out: str | Path = DEFAULT_DIR, seed: int = 42, force: bool = False, reference: date | None = None) -> BuildResult:
    started = time.perf_counter()
    out = Path(out)
    db_path = out / DB_NAME
    if db_path.exists() and not force:
        raise FileExistsError(f"{db_path} already exists; pass --force to rebuild it")
    out.mkdir(parents=True, exist_ok=True)
    _clean(out)

    hh = household.generate(seed, reference)
    files = writers.write_all(hh, out / "statements")
    (out / "sources.toml").write_text(_sources_toml(hh), encoding="utf-8")
    (out / "rules.json").write_text(rules_io.dumps(DEMO_RULES), encoding="utf-8")

    conn = db.connect(db_path)
    conn.execute("PRAGMA synchronous = OFF")      # a throwaway database built in one go: skip the per-commit fsyncs
    try:
        household_mod.set_name(conn, hh.name)
        household_mod.set_people(conn, hh.people)
        config = out / "sources.toml"
        results = sync_mod.sync(conn, sync_mod.load_sources(config))
        failed = [f"{r.source}: {r.message}" for r in results if r.status == "error"]
        if failed:
            raise RuntimeError("demo statements did not import:\n  " + "\n  ".join(failed))
        summary = rules_io.import_file(conn, sync_mod.rules_file(config))
        if summary.errors:
            raise RuntimeError("demo rules did not import:\n  " + "\n  ".join(summary.errors))
        categorise.apply_rules(conn)

        holdings = _holdings(conn, hh)
        _snapshot(conn, hh)
        for name, target in TARGETS.items():
            reports.set_budget(conn, db.category_id_by_path(conn, name), target)
        with conn:
            conn.execute("UPDATE transactions SET one_off = 1 WHERE description LIKE 'MOTORCARE%' AND amount < -1000")
        months = [f"{y}-{m:02d}" for y, m in hh.months]
        for month in months[:-1]:
            review.close_month(conn, month)
        _scenario(conn, holdings)
        overview.set_fund_target(conn, 80000.0)

        n, uncat = conn.execute("SELECT COUNT(*), SUM(category_id IS NULL) FROM transactions").fetchone()
        n_rules = conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
    finally:
        conn.close()
    return BuildResult(out=out, db_path=db_path, files=files, transactions=n, uncategorised=uncat or 0,
                       rules=n_rules, seconds=time.perf_counter() - started, months=months)


def ensure(out: str | Path = DEFAULT_DIR) -> Path:
    """The demo database, built first if it is missing (used by `slopfi serve --demo`)."""
    db_path = Path(out) / DB_NAME
    if not db_path.exists():
        build(out)
    return db_path
