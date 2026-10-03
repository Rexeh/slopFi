"""Rule-based categorisation with manual overrides."""
from __future__ import annotations

import re
import sqlite3
from functools import lru_cache

from .db import now_iso

NUMERIC_TOKEN = re.compile(r"^[\d\-*#@:.]+$")

# How a source's own category labels (e.g. Monzo's) map onto our taxonomy. Used only when no rule matches.
SOURCE_CATEGORY_MAP = {
    "Eating out": "Eating out",
    "Groceries": "Groceries",
    "Transport": "Transport",
    "Shopping": "Shopping",
    "Bills": "Utilities",
    "Entertainment": "Leisure",
    "Holidays": "Leisure/Holidays",
    "Personal care": "Health & personal care",
    "Income": "Income/Other income",
    "Transfers": "Transfers/Between own accounts",
    "Savings": "Transfers/Savings",
    "Family": "Children",
    "Finances": "Transfers/Between own accounts",
    "Charity": "Other spending",
    "Gifts": "Shopping/Gifts",
    "General": None,
    "Expenses": None,
}


def normalise(description: str) -> str:
    return re.sub(r"\s+", " ", description.upper()).strip()


def suggest_pattern(description: str) -> str:
    """A reasonable 'contains' pattern for a description: the first line with pure-number tokens removed."""
    lines = [l.strip() for l in description.split(" / ") if l.strip()]
    for line in lines:
        if line.upper().startswith("INT'L") or line.startswith("EUR ") or line.upper().startswith("VISA RATE"):
            continue
        tokens = [t for t in line.split() if not NUMERIC_TOKEN.match(t)]
        if tokens:
            return normalise(" ".join(tokens))
    return normalise(lines[0]) if lines else ""


@lru_cache(maxsize=512)
def _regex(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


def rule_matches(rule: sqlite3.Row | dict, description: str, type_code: str | None, amount: float) -> bool:
    text = normalise(description)
    pattern = rule["pattern"]
    mt = rule["match_type"]
    if mt == "contains":
        ok = normalise(pattern) in text
    elif mt == "prefix":
        ok = text.startswith(normalise(pattern))
    elif mt == "regex":
        ok = _regex(pattern).search(description) is not None
    else:
        ok = False
    if not ok:
        return False
    if rule["type_code"] and rule["type_code"] != type_code:
        return False
    if rule["amount_min"] is not None and amount < rule["amount_min"]:
        return False
    if rule["amount_max"] is not None and amount > rule["amount_max"]:
        return False
    return True


def load_rules(conn: sqlite3.Connection, enabled_only: bool = True) -> list[sqlite3.Row]:
    sql = "SELECT * FROM rules"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY priority ASC, id ASC"
    return conn.execute(sql).fetchall()


def find_rule(rules: list[sqlite3.Row], description: str, type_code: str | None, amount: float):
    for rule in rules:
        if rule_matches(rule, description, type_code, amount):
            return rule
    return None


def apply_rules(
    conn: sqlite3.Connection,
    transaction_ids: list[int] | None = None,
    include_rule_categorised: bool = False,
) -> int:
    """Categorise transactions using rules. Manual categorisations are never touched.

    By default only uncategorised transactions are considered; with
    include_rule_categorised=True, rule-categorised ones are re-evaluated too.
    """
    rules = load_rules(conn)
    where = ["(categorised_by IS NULL OR categorised_by = 'source')"] if not include_rule_categorised \
        else ["(categorised_by IS NULL OR categorised_by IN ('rule', 'source'))"]
    params: list = []
    if transaction_ids is not None:
        if not transaction_ids:
            return 0
        where.append(f"id IN ({','.join('?' * len(transaction_ids))})")
        params.extend(transaction_ids)
    rows = conn.execute(
        f"SELECT id, description, type_code, amount FROM transactions WHERE {' AND '.join(where)}", params
    ).fetchall()
    changed = 0
    with conn:
        for row in rows:
            rule = find_rule(rules, row["description"], row["type_code"], row["amount"])
            if rule is not None:
                conn.execute(
                    "UPDATE transactions SET category_id = ?, categorised_by = 'rule', rule_id = ? WHERE id = ?",
                    (rule["category_id"], rule["id"], row["id"]),
                )
                changed += 1
            elif include_rule_categorised:
                conn.execute(
                    "UPDATE transactions SET category_id = NULL, categorised_by = NULL, rule_id = NULL "
                    "WHERE id = ? AND categorised_by = 'rule'",
                    (row["id"],),
                )
    return changed


def apply_source_suggestions(conn: sqlite3.Connection, suggestions: dict[int, str]) -> int:
    """Fallback categorisation from the source's own labels for transactions still uncategorised."""
    from .db import category_id_by_path

    changed = 0
    with conn:
        for txn_id, label in suggestions.items():
            path = SOURCE_CATEGORY_MAP.get(label)
            if not path:
                continue
            cat_id = category_id_by_path(conn, path)
            if cat_id is None:
                continue
            cur = conn.execute(
                "UPDATE transactions SET category_id = ?, categorised_by = 'source', rule_id = NULL "
                "WHERE id = ? AND categorised_by IS NULL",
                (cat_id, txn_id),
            )
            changed += cur.rowcount
    return changed


def set_category(conn: sqlite3.Connection, transaction_id: int, category_id: int | None) -> None:
    with conn:
        if category_id is None:
            conn.execute(
                "UPDATE transactions SET category_id = NULL, categorised_by = NULL, rule_id = NULL WHERE id = ?",
                (transaction_id,),
            )
        else:
            conn.execute(
                "UPDATE transactions SET category_id = ?, categorised_by = 'manual', rule_id = NULL WHERE id = ?",
                (category_id, transaction_id),
            )


def create_rule(
    conn: sqlite3.Connection,
    pattern: str,
    category_id: int,
    match_type: str = "contains",
    type_code: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    priority: int = 50,
    source: str = "manual",
) -> int:
    pattern = pattern.strip()
    if not pattern:
        raise ValueError("pattern must not be empty")
    if match_type == "regex":
        re.compile(pattern)  # raises re.error if invalid
    with conn:
        cur = conn.execute(
            """INSERT INTO rules (priority, match_type, pattern, type_code, amount_min, amount_max,
                                  category_id, source, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (priority, match_type, pattern, type_code or None, amount_min, amount_max, category_id, source, now_iso()),
        )
        return cur.lastrowid
