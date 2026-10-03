"""SQLite connection, schema and seeding: categories, plus built-in rules once per database."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL CHECK (kind IN ('current', 'credit_card', 'savings')),
    owner       TEXT NOT NULL,
    institution TEXT,
    identifier  TEXT,
    currency    TEXT NOT NULL DEFAULT 'GBP',
    UNIQUE (institution, identifier)
);

CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    parent_id  INTEGER REFERENCES categories(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('expense', 'income', 'transfer')),
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS categories_name_parent
    ON categories (name, COALESCE(parent_id, 0));

CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY,
    priority    INTEGER NOT NULL DEFAULT 100,
    match_type  TEXT NOT NULL CHECK (match_type IN ('contains', 'prefix', 'regex')),
    pattern     TEXT NOT NULL,
    type_code   TEXT,
    amount_min  REAL,
    amount_max  REAL,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    source      TEXT NOT NULL DEFAULT 'manual',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS statements (
    id                INTEGER PRIMARY KEY,
    account_id        INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    source_name       TEXT NOT NULL,
    file_sha256       TEXT NOT NULL UNIQUE,
    parser            TEXT NOT NULL,
    period_start      TEXT NOT NULL,
    period_end        TEXT NOT NULL,
    opening_balance   REAL,
    closing_balance   REAL,
    payments_in       REAL,
    payments_out      REAL,
    transaction_count INTEGER NOT NULL DEFAULT 0,
    warnings          TEXT NOT NULL DEFAULT '[]',
    imported_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id             INTEGER PRIMARY KEY,
    statement_id   INTEGER NOT NULL REFERENCES statements(id) ON DELETE CASCADE,
    account_id     INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    seq            INTEGER NOT NULL,
    date           TEXT NOT NULL,
    type_code      TEXT,
    description    TEXT NOT NULL,
    raw_lines      TEXT NOT NULL,
    amount         REAL NOT NULL,
    balance_after  REAL,
    fx_currency    TEXT,
    fx_amount      REAL,
    fx_rate        REAL,
    fingerprint    TEXT NOT NULL UNIQUE,
    category_id    INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    categorised_by TEXT CHECK (categorised_by IN ('rule', 'manual', 'source')),
    rule_id        INTEGER REFERENCES rules(id) ON DELETE SET NULL,
    notes          TEXT,
    one_off        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS transactions_date ON transactions (date);
CREATE INDEX IF NOT EXISTS transactions_category ON transactions (category_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS holdings (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('property', 'mortgage', 'pension', 'stocks', 'stocks_isa', 'cash_isa',
                                                 'cash', 'loan', 'other_asset', 'other_liability')),
    owner           TEXT NOT NULL DEFAULT 'joint',
    provider        TEXT,
    value           REAL NOT NULL,            -- current value (assets) or balance outstanding (liabilities), positive
    valued_at       TEXT NOT NULL,            -- date the value was last updated
    rate            REAL,                     -- annual interest rate %, for mortgages, loans and cash
    monthly_payment REAL,                     -- for mortgages and loans
    fix_end         TEXT,                     -- date a fixed rate ends
    term_end        TEXT,                     -- date the loan is due to be repaid
    notes           TEXT,
    created_at      TEXT NOT NULL,
    parent_id       INTEGER REFERENCES holdings(id) ON DELETE CASCADE   -- a mortgage linked to its property
);

CREATE TABLE IF NOT EXISTS holding_snapshots (
    id         INTEGER PRIMARY KEY,
    holding_id INTEGER NOT NULL REFERENCES holdings(id) ON DELETE CASCADE,
    date       TEXT NOT NULL,
    value      REAL NOT NULL,
    UNIQUE (holding_id, date)
);

CREATE TABLE IF NOT EXISTS balance_snapshots (
    id         INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    date       TEXT NOT NULL,
    balance    REAL NOT NULL,
    source     TEXT NOT NULL DEFAULT 'manual',
    UNIQUE (account_id, date, source)
);

CREATE TABLE IF NOT EXISTS scenarios (
    id                        INTEGER PRIMARY KEY,
    name                      TEXT NOT NULL UNIQUE,
    is_default                INTEGER NOT NULL DEFAULT 0,
    horizon_years             INTEGER,            -- NULL = settings default
    inflation_pct             REAL,               -- NULL = settings default
    property_growth_pct       REAL,               -- NULL = settings default
    mortgage_rate_after_fix   REAL,               -- NULL = keep the current rate
    mortgage_overpayment      REAL,               -- NULL = observed
    contribution_growth_pct   REAL NOT NULL DEFAULT 0,
    notes                     TEXT,
    created_at                TEXT NOT NULL,
    surplus_mode              TEXT NOT NULL DEFAULT 'ignore',   -- ignore | save
    surplus_override          REAL,                             -- £/month; NULL = derived from income − outgoings − contributions
    surplus_target            TEXT,                             -- item key the surplus and category savings go to; NULL = Unallocated cash
    category_savings          TEXT NOT NULL DEFAULT '{}'        -- json {category_id: percent}
);

CREATE TABLE IF NOT EXISTS scenario_items (
    id                   INTEGER PRIMARY KEY,
    scenario_id          INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    ref_type             TEXT NOT NULL CHECK (ref_type IN ('holding', 'account')),
    ref_id               INTEGER NOT NULL,
    annual_growth_pct    REAL,                    -- NULL = kind default from settings
    monthly_contribution REAL,                    -- NULL = observed from history
    contribution_until   TEXT,                    -- NULL = indefinitely
    UNIQUE (scenario_id, ref_type, ref_id)
);

CREATE TABLE IF NOT EXISTS budgets (
    category_id    INTEGER PRIMARY KEY REFERENCES categories(id) ON DELETE CASCADE,
    monthly_target REAL NOT NULL,
    note           TEXT,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS closed_months (
    month     TEXT PRIMARY KEY,          -- 'YYYY-MM'; a month the household has finished reviewing
    closed_at TEXT NOT NULL
);
"""

# Applied in order to databases created at an earlier SCHEMA_VERSION. New tables come from SCHEMA.
# Each step must be safe to re-run: an older build of the app may have re-stamped the version.
def _add_column(table: str, column: str, definition: str):
    def step(conn: sqlite3.Connection) -> None:
        if column not in [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    return step


MIGRATIONS: dict[int, list] = {
    2: [_add_column("transactions", "one_off", "INTEGER NOT NULL DEFAULT 0")],
    3: [],   # new tables only; created by SCHEMA
    4: [_add_column("holdings", "parent_id", "INTEGER REFERENCES holdings(id) ON DELETE CASCADE")],
    5: [],   # new tables only
    6: [_add_column("scenarios", "surplus_mode", "TEXT NOT NULL DEFAULT 'ignore'"),
        _add_column("scenarios", "surplus_override", "REAL"),
        _add_column("scenarios", "surplus_target", "TEXT"),
        _add_column("scenarios", "category_savings", "TEXT NOT NULL DEFAULT '{}'")],
    7: [],   # closed_months: new table only
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_db_path() -> Path:
    return Path(os.environ.get("SLOPFI_DB", "slopfi.db"))


def connect(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """Open (and initialise if needed) the database. Use ':memory:' for tests."""
    target = ":memory:" if path == ":memory:" else str(path or default_db_path())
    conn = sqlite3.connect(target, detect_types=0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL") if target != ":memory:" else None
    init_db(conn)
    return conn


SCHEMA_VERSION = 7


def init_db(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    has_tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] > 0
    if has_tables and version == 0:
        raise RuntimeError("database predates schema versioning; delete the .db file and run `slopfi sync`")
    if has_tables and version > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {version} is newer than this build supports ({SCHEMA_VERSION}); "
            "update the app (uv sync) rather than running an older build against it"
        )
    if has_tables:
        for v in range(version + 1, SCHEMA_VERSION + 1):
            for step in MIGRATIONS.get(v, []):
                step(conn)
    conn.executescript(SCHEMA)
    if version < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    seed_categories(conn)  # inserts only categories not already present
    seed_rules(conn)


def _seed_file(name: str) -> list | dict:
    return json.loads(resources.files("slopfi.seed").joinpath(name).read_text())


def seed_categories(conn: sqlite3.Connection) -> None:
    data = _seed_file("categories.json")
    with conn:
        for order, top in enumerate(data):
            row = conn.execute("SELECT id FROM categories WHERE name = ? AND parent_id IS NULL", (top["name"],)).fetchone()
            if row:
                parent_id = row["id"]
            else:
                parent_id = conn.execute(
                    "INSERT INTO categories (name, parent_id, kind, sort_order) VALUES (?, NULL, ?, ?)",
                    (top["name"], top["kind"], order),
                ).lastrowid
            for sub_order, child in enumerate(top.get("children", [])):
                conn.execute(
                    "INSERT OR IGNORE INTO categories (name, parent_id, kind, sort_order) VALUES (?, ?, ?, ?)",
                    (child, parent_id, top["kind"], sub_order),
                )


# Bump when seed/rules.json changes so existing databases pick up the new rules (duplicates are skipped).
SEED_RULES_VERSION = 3


def seed_rules(conn: sqlite3.Connection) -> None:
    """Built-in rules for common UK trading names, added once per database. The names come from the lists banks
    publish to explain unfamiliar statement entries (Nationwide, Lloyds/Halifax, RBS/NatWest, Bank of Ireland).

    They sit at priority 200 (catch-alls like Amazon at 210) so manual (50) and imported (100) rules win. A built-in
    rule the user deletes stays deleted until SEED_RULES_VERSION is bumped."""
    from .rules_io import import_rules

    if int(get_setting(conn, "seed_rules_version", "0")) >= SEED_RULES_VERSION:
        return
    import_rules(conn, _seed_file("rules.json"), source="seed")
    set_setting(conn, "seed_rules_version", str(SEED_RULES_VERSION))


def category_id_by_path(conn: sqlite3.Connection, path: str) -> int | None:
    """Resolve 'Parent/Child' or 'Parent' to a category id."""
    parts = [p.strip() for p in path.split("/")]
    row = conn.execute(
        "SELECT id FROM categories WHERE name = ? AND parent_id IS NULL", (parts[0],)
    ).fetchone()
    if row is None:
        return None
    if len(parts) == 1:
        return row["id"]
    child = conn.execute(
        "SELECT id FROM categories WHERE name = ? AND parent_id = ?", (parts[1], row["id"])
    ).fetchone()
    return child["id"] if child else None


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
