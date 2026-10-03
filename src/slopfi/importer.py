"""Import parsed statements into the database (idempotent)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import categorise
from .db import now_iso
from .parsers import ParsedStatement, ParseError, parse_file_all


@dataclass
class AccountSpec:
    """How to find or create the account a file belongs to (used by sync)."""
    name: str
    owner: str = "unknown"
    kind: str | None = None   # defaults to what the parser reports


@dataclass
class ImportResult:
    source: str
    status: str                     # imported | duplicate | error
    statement_id: int | None = None
    inserted: int = 0
    skipped: int = 0
    categorised: int = 0
    message: str = ""
    warnings: list[str] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_account(conn: sqlite3.Connection, stmt: ParsedStatement) -> int:
    if stmt.requires_account:
        raise ValueError("this file does not identify its account; choose one")
    row = conn.execute(
        "SELECT id FROM accounts WHERE institution = ? AND identifier = ?",
        (stmt.institution, stmt.account_identifier),
    ).fetchone()
    if row:
        return row["id"]
    owner = "joint" if "&" in stmt.account_name else "unknown"
    last4 = stmt.account_identifier[-4:] if stmt.account_identifier else "?"
    name = f"{stmt.institution} {stmt.account_kind.replace('_', ' ')} …{last4}"
    cur = conn.execute(
        "INSERT INTO accounts (name, kind, owner, institution, identifier) VALUES (?, ?, ?, ?, ?)",
        (name, stmt.account_kind, owner, stmt.institution, stmt.account_identifier),
    )
    return cur.lastrowid


def import_statement(
    conn: sqlite3.Connection, stmt: ParsedStatement, source_name: str, file_hash: str,
    account_id: int | None = None,
) -> ImportResult:
    result = ImportResult(source=source_name, status="imported", warnings=list(stmt.warnings))
    seen: Counter[tuple] = Counter()
    with conn:
        if account_id is None:
            account_id = ensure_account(conn, stmt)
        acct = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if acct is None:
            raise ValueError(f"no account with id {account_id}")
        if acct["kind"] != stmt.account_kind:
            result.warnings.append(f"file looks like a {stmt.account_kind} account but {acct['name']} is {acct['kind']}")
        if stmt.account_identifier:
            # Attach the bank's identifier to a hand-made account, and refuse files from a different account.
            if not acct["identifier"]:
                conn.execute("UPDATE accounts SET institution = ?, identifier = ? WHERE id = ?",
                             (stmt.institution, stmt.account_identifier, account_id))
            elif (acct["institution"], acct["identifier"]) != (stmt.institution, stmt.account_identifier):
                raise ValueError(
                    f"{source_name} is for {stmt.institution} {stmt.account_identifier}, "
                    f"but {acct['name']} is {acct['institution']} {acct['identifier']}"
                )
        # Fingerprint base: the bank identifier when the format has one, else the chosen account.
        fp_base = f"{stmt.institution}|{stmt.account_identifier}" if stmt.account_identifier else f"account:{account_id}"
        cur = conn.execute(
            """INSERT INTO statements (account_id, source_name, file_sha256, parser, period_start, period_end,
                                       opening_balance, closing_balance, payments_in, payments_out,
                                       transaction_count, warnings, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                account_id, source_name, file_hash, stmt.parser,
                stmt.period_start.isoformat(), stmt.period_end.isoformat(),
                stmt.opening_balance, stmt.closing_balance, stmt.payments_in, stmt.payments_out,
                len(stmt.transactions), json.dumps(stmt.warnings), now_iso(),
            ),
        )
        statement_id = cur.lastrowid
        result.statement_id = statement_id
        new_ids: list[int] = []
        suggestions: dict[int, str] = {}
        for seq, t in enumerate(stmt.transactions, start=1):
            key = (t.date.isoformat(), round(t.amount or 0.0, 2), t.description)
            occurrence = seen[key]
            seen[key] += 1
            if t.external_id:
                fp_source = f"{fp_base}|id:{t.external_id}"
            else:
                fp_source = f"{fp_base}|{key[0]}|{key[1]:.2f}|{key[2]}|{occurrence}"
            fingerprint = hashlib.sha256(fp_source.encode()).hexdigest()
            cur = conn.execute(
                """INSERT OR IGNORE INTO transactions
                   (statement_id, account_id, seq, date, type_code, description, raw_lines, amount,
                    balance_after, fx_currency, fx_amount, fx_rate, fingerprint)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    statement_id, account_id, seq, t.date.isoformat(), t.type_code, t.description,
                    json.dumps(t.lines), round(t.amount or 0.0, 2), t.balance_after,
                    t.fx_currency, t.fx_amount, t.fx_rate, fingerprint,
                ),
            )
            if cur.rowcount:
                new_ids.append(cur.lastrowid)
                if t.suggested_category:
                    suggestions[cur.lastrowid] = t.suggested_category
            else:
                result.skipped += 1
        result.inserted = len(new_ids)
        result.categorised = categorise.apply_rules(conn, transaction_ids=new_ids)
        result.categorised += categorise.apply_source_suggestions(conn, suggestions)
    return result


def import_file(
    conn: sqlite3.Connection, path: str | Path, source_name: str | None = None, account_id: int | None = None,
    account: AccountSpec | None = None,
) -> list[ImportResult]:
    """Import every statement in a file. One result per statement (a Monzo PDF has one per pot)."""
    path = Path(path)
    name = source_name or path.name
    file_hash = sha256_file(path)
    existing = conn.execute("SELECT id FROM statements WHERE file_sha256 = ? OR file_sha256 LIKE ?",
                            (file_hash, file_hash + "#%")).fetchone()
    if existing:
        return [ImportResult(source=name, status="duplicate", statement_id=existing["id"],
                             message="already imported (identical file)")]
    try:
        stmts = parse_file_all(path)
    except ParseError as exc:
        return [ImportResult(source=name, status="error", message=str(exc))]
    results = []
    for index, stmt in enumerate(stmts):
        section_hash = file_hash if index == 0 else f"{file_hash}#{index}"
        label = f"{name} [{stmt.sub_account}]" if stmt.sub_account else name
        target = account_id
        if stmt.sub_account:
            # a pot: always its own account, found or created by its identifier; owner follows the spec
            target = None
            row = conn.execute("SELECT id FROM accounts WHERE institution = ? AND identifier = ?",
                               (stmt.institution, stmt.account_identifier)).fetchone()
            if row:
                target = row["id"]
            else:
                target = get_or_create_account(conn, stmt.account_name, account.owner if account else "unknown",
                                               "savings", institution=stmt.institution)
                with conn:
                    conn.execute("UPDATE accounts SET identifier = ? WHERE id = ?", (stmt.account_identifier, target))
        elif account is not None and target is None:
            target = get_or_create_account(conn, account.name, account.owner, account.kind or stmt.account_kind,
                                           institution=stmt.institution if stmt.account_identifier else None)
        if stmt.requires_account and target is None:
            results.append(ImportResult(source=label, status="error",
                                        message="this export does not say which account it is from: choose an account and import again"))
            continue
        try:
            results.append(import_statement(conn, stmt, label, section_hash, account_id=target))
        except ValueError as exc:
            results.append(ImportResult(source=label, status="error", message=str(exc)))
    return results


def get_or_create_account(conn: sqlite3.Connection, name: str, owner: str = "unknown", kind: str = "current",
                          institution: str | None = None) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    with conn:
        cur = conn.execute("INSERT INTO accounts (name, kind, owner, institution) VALUES (?, ?, ?, ?)",
                           (name, kind, owner, institution))
        return cur.lastrowid


def delete_statement(conn: sqlite3.Connection, statement_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM statements WHERE id = ?", (statement_id,))


def delete_account(conn: sqlite3.Connection, account_id: int) -> bool:
    """Delete an account that has no statements left. Returns False if it still has data."""
    n = conn.execute("SELECT COUNT(*) FROM statements WHERE account_id = ?", (account_id,)).fetchone()[0]
    if n:
        return False
    with conn:
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    return True
