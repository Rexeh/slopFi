"""The household: its name and the people who own accounts, kept in the settings table.

Owner keys on accounts and holdings are 'joint', 'unknown' or a person's key. A database whose accounts use
keys that are not in the people list (one made before this setting existed) still shows them: such keys are
added to the list, title-cased, the first time the household is read.
"""
from __future__ import annotations

import json
import re
import sqlite3

from .db import get_setting, set_setting

NAME_KEY = "household.name"
PEOPLE_KEY = "household.people"
DEFAULT_NAME = "My household"
RESERVED = {"joint": "Joint", "unknown": "Unknown"}


def _owner_keys_in_use(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT owner FROM accounts UNION SELECT owner FROM holdings").fetchall()
    return sorted({r[0] for r in rows if r[0] and r[0] not in RESERVED})


def _read_people(conn: sqlite3.Connection) -> list[dict]:
    raw = get_setting(conn, PEOPLE_KEY)
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        data = []
    people = []
    for p in data if isinstance(data, list) else []:
        if isinstance(p, dict) and p.get("key") and p["key"] not in RESERVED:
            people.append({"key": str(p["key"]), "name": str(p.get("name") or p["key"]).strip() or str(p["key"])})
    return people


def _write_people(conn: sqlite3.Connection, people: list[dict]) -> None:
    set_setting(conn, PEOPLE_KEY, json.dumps([{"key": p["key"], "name": p["name"]} for p in people]))


def people(conn: sqlite3.Connection) -> list[dict]:
    """[{key, name}], with any owner key found on accounts or holdings but missing from the list added (and saved)."""
    current = _read_people(conn)
    known = {p["key"] for p in current}
    missing = [k for k in _owner_keys_in_use(conn) if k not in known]
    if missing:
        current += [{"key": k, "name": k.replace("_", " ").title()} for k in missing]
        _write_people(conn, current)
    return current


def name(conn: sqlite3.Connection) -> str:
    return (get_setting(conn, NAME_KEY) or "").strip() or DEFAULT_NAME


def get(conn: sqlite3.Connection) -> dict:
    return {"name": name(conn), "people": people(conn)}


def owner_choices(conn: sqlite3.Connection) -> dict[str, str]:
    """Every owner a select offers, in order: Joint, each person, Unknown."""
    return {"joint": "Joint", **{p["key"]: p["name"] for p in people(conn)}, "unknown": "Unknown"}


def owner_label(choices: dict[str, str], key: str | None) -> str:
    return choices.get(key or "", (key or "unknown").replace("_", " ").title())


def join_names(names: list[str]) -> str:
    """'Alex' / 'Alex and Sam' / 'Alex, Sam and Jo'."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def subtitle(conn: sqlite3.Connection) -> str:
    """The line under the brand: 'Household · Alex and Sam', or the household name when there are no people."""
    names = [p["name"] for p in people(conn)]
    return f"Household · {join_names(names)}" if names else name(conn)


# ------------------------------------------------------------------ editing
def set_name(conn: sqlite3.Connection, value: str) -> None:
    set_setting(conn, NAME_KEY, value.strip())


def make_key(conn: sqlite3.Connection, display_name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", display_name.strip().lower()).strip("_") or "person"
    taken = {p["key"] for p in people(conn)} | set(RESERVED) | set(_owner_keys_in_use(conn))
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


def add_person(conn: sqlite3.Connection, display_name: str, key: str | None = None) -> dict:
    display_name = display_name.strip()
    if not display_name:
        raise ValueError("Give the person a name.")
    current = people(conn)
    if any(p["name"].lower() == display_name.lower() for p in current):
        raise ValueError(f"{display_name} is already in the household.")
    key = key or make_key(conn, display_name)
    if key in RESERVED or any(p["key"] == key for p in current):
        raise ValueError(f"The key {key} is already used.")
    person = {"key": key, "name": display_name}
    _write_people(conn, current + [person])
    return person


def rename_person(conn: sqlite3.Connection, key: str, display_name: str) -> None:
    display_name = display_name.strip()
    if not display_name:
        raise ValueError("Give the person a name.")
    current = people(conn)
    if not any(p["key"] == key for p in current):
        raise KeyError(key)
    if any(p["name"].lower() == display_name.lower() and p["key"] != key for p in current):
        raise ValueError(f"{display_name} is already in the household.")
    _write_people(conn, [{**p, "name": display_name} if p["key"] == key else p for p in current])


def owned_counts(conn: sqlite3.Connection, key: str) -> dict[str, int]:
    accounts = conn.execute("SELECT COUNT(*) FROM accounts WHERE owner = ?", (key,)).fetchone()[0]
    holdings = conn.execute("SELECT COUNT(*) FROM holdings WHERE owner = ?", (key,)).fetchone()[0]
    return {"accounts": accounts, "holdings": holdings}


def remove_person(conn: sqlite3.Connection, key: str) -> bool:
    """Remove a person who owns nothing. Returns False (and changes nothing) while they own accounts or assets."""
    counts = owned_counts(conn, key)
    if counts["accounts"] or counts["holdings"]:
        return False
    _write_people(conn, [p for p in people(conn) if p["key"] != key])
    return True


def set_people(conn: sqlite3.Connection, people_list: list[dict]) -> None:
    """Replace the list outright (the demo builder uses this)."""
    _write_people(conn, [{"key": p["key"], "name": p["name"]} for p in people_list if p["key"] not in RESERVED])
