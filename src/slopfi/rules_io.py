"""Rules export and import as JSON.

The file is a list of objects:
    {"pattern": "TESCO", "match_type": "contains", "category": "Groceries/Supermarket", "priority": 50,
     "type_code": "DD", "amount_min": -100.0, "amount_max": -5.0, "enabled": true}
`type_code`, `amount_min`, `amount_max` and `enabled` are optional; `match_type` defaults to contains and
`priority` to 100. `category` is "Parent/Child" or a top-level "Parent".
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .db import category_id_by_path, now_iso

MATCH_TYPES = ("contains", "prefix", "regex")


@dataclass
class ImportSummary:
    added: int = 0
    duplicates: int = 0
    removed: int = 0
    categories_created: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def skipped(self) -> int:
        return self.duplicates + len(self.errors)


def _category_path(conn: sqlite3.Connection, category_id: int) -> str:
    row = conn.execute(
        "SELECT c.name, p.name AS parent FROM categories c LEFT JOIN categories p ON p.id = c.parent_id WHERE c.id = ?",
        (category_id,),
    ).fetchone()
    return f"{row['parent']}/{row['name']}" if row["parent"] else row["name"]


def export_rules(conn: sqlite3.Connection) -> list[dict]:
    """Every rule in priority order, in the file format."""
    out = []
    for r in conn.execute("SELECT * FROM rules ORDER BY priority, id"):
        item: dict = {"pattern": r["pattern"], "match_type": r["match_type"],
                      "category": _category_path(conn, r["category_id"]), "priority": r["priority"]}
        if r["type_code"]:
            item["type_code"] = r["type_code"]
        if r["amount_min"] is not None:
            item["amount_min"] = r["amount_min"]
        if r["amount_max"] is not None:
            item["amount_max"] = r["amount_max"]
        item["enabled"] = bool(r["enabled"])
        out.append(item)
    return out


def dumps(rules: list[dict]) -> str:
    return json.dumps(rules, indent=2, ensure_ascii=False) + "\n"


def export_file(conn: sqlite3.Connection, path: str | Path) -> int:
    rules = export_rules(conn)
    Path(path).write_text(dumps(rules), encoding="utf-8")
    return len(rules)


def _resolve_category(conn: sqlite3.Connection, path: str, created: list[str]) -> int | None:
    """The category for 'Parent/Child', creating Child under an existing Parent. None if the parent is unknown."""
    cat_id = category_id_by_path(conn, path)
    if cat_id is not None:
        return cat_id
    parts = [p.strip() for p in path.split("/")]
    if len(parts) != 2 or not parts[1]:
        return None
    parent = conn.execute("SELECT id, kind FROM categories WHERE name = ? AND parent_id IS NULL", (parts[0],)).fetchone()
    if parent is None:
        return None
    cur = conn.execute("INSERT INTO categories (name, parent_id, kind, sort_order) VALUES (?, ?, ?, 99)",
                       (parts[1], parent["id"], parent["kind"]))
    created.append(f"{parts[0]}/{parts[1]}")
    return cur.lastrowid


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def parse(text: str) -> list[dict]:
    """Decode a rules file; raises ValueError when it is not a list of rule objects."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"not valid JSON: {exc}") from None
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise ValueError("a rules file is a JSON list of rule objects")
    return data


def import_rules(conn: sqlite3.Connection, rules: list[dict], replace: bool = False, source: str = "import") -> ImportSummary:
    """Add each rule unless an identical one exists. With replace=True every existing rule is deleted first.

    A missing sub-category is created when its parent exists; a rule naming an unknown parent is skipped and
    reported in `errors`. Nothing is categorised here: call categorise.apply_rules afterwards."""
    summary = ImportSummary()
    with conn:
        if replace:
            summary.removed = conn.execute("DELETE FROM rules").rowcount
        for n, rule in enumerate(rules, start=1):
            pattern = str(rule.get("pattern") or "").strip()
            match_type = rule.get("match_type") or "contains"
            category = str(rule.get("category") or "").strip()
            label = f"rule {n} ({pattern or 'no pattern'})"
            if not pattern:
                summary.errors.append(f"{label}: no pattern")
                continue
            if match_type not in MATCH_TYPES:
                summary.errors.append(f"{label}: unknown match type {match_type!r}")
                continue
            if match_type == "regex":
                try:
                    re.compile(pattern)
                except re.error as exc:
                    summary.errors.append(f"{label}: expression does not compile ({exc})")
                    continue
            if not category:
                summary.errors.append(f"{label}: no category")
                continue
            try:
                amount_min, amount_max = _num(rule.get("amount_min")), _num(rule.get("amount_max"))
                priority = int(rule.get("priority", 100))
            except (TypeError, ValueError):
                summary.errors.append(f"{label}: priority and amounts must be numbers")
                continue
            cat_id = _resolve_category(conn, category, summary.categories_created)
            if cat_id is None:
                summary.errors.append(f"{label}: unknown category {category!r}")
                continue
            type_code = (str(rule.get("type_code") or "").strip()) or None
            exists = conn.execute(
                """SELECT 1 FROM rules WHERE pattern = ? AND match_type = ? AND COALESCE(type_code, '') = ?
                   AND category_id = ? AND amount_min IS ? AND amount_max IS ?""",
                (pattern, match_type, type_code or "", cat_id, amount_min, amount_max),
            ).fetchone()
            if exists:
                summary.duplicates += 1
                continue
            conn.execute(
                """INSERT INTO rules (priority, match_type, pattern, type_code, amount_min, amount_max,
                                      category_id, source, enabled, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (priority, match_type, pattern, type_code, amount_min, amount_max, cat_id, source,
                 0 if rule.get("enabled") is False else 1, now_iso()),
            )
            summary.added += 1
    return summary


def import_file(conn: sqlite3.Connection, path: str | Path, replace: bool = False) -> ImportSummary:
    return import_rules(conn, parse(Path(path).read_text(encoding="utf-8")), replace=replace)


def describe(summary: ImportSummary) -> str:
    """One sentence for the CLI and the toast."""
    def plural(n: int, noun: str) -> str:
        return f"{n} {noun}" + ("" if n == 1 else "s")

    parts = [f"{plural(summary.added, 'rule')} imported"]
    if summary.removed:
        parts.append(f"{plural(summary.removed, 'old rule')} replaced")
    if summary.duplicates:
        parts.append(f"{plural(summary.duplicates, 'duplicate')} skipped")
    if summary.errors:
        parts.append(f"{plural(len(summary.errors), 'rule')} not imported")
    if summary.categories_created:
        parts.append(f"{plural(len(summary.categories_created), 'category')}".replace("categorys", "categories")
                     + " created")
    return ", ".join(parts)
