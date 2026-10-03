"""Import everything listed in sources.toml."""
from __future__ import annotations

import os
import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import importer

FILE_TYPES = (".pdf", ".csv")


@dataclass
class Source:
    path: Path
    account: str
    owner: str = "unknown"
    kind: str | None = None


def default_config_path() -> Path:
    return Path(os.environ.get("SLOPFI_SOURCES", "sources.toml"))


def load_sources(config_path: str | os.PathLike | None = None) -> list[Source]:
    path = Path(config_path or default_config_path())
    if not path.exists():
        raise FileNotFoundError(f"no sources file at {path}")
    data = tomllib.loads(path.read_text())
    sources = []
    for entry in data.get("source", []):
        if "path" not in entry or "account" not in entry:
            raise ValueError(f"each [[source]] needs 'path' and 'account': {entry}")
        sources.append(Source(
            path=(path.parent / entry["path"]).resolve(), account=entry["account"],
            owner=entry.get("owner", "unknown"), kind=entry.get("kind"),
        ))
    return sources


def rules_file(config_path: str | os.PathLike | None = None) -> Path | None:
    """The `rules_file` named at the top of sources.toml (relative to that file), or None when there is none."""
    path = Path(config_path or default_config_path())
    if not path.exists():
        return None
    value = tomllib.loads(path.read_text()).get("rules_file")
    if not value:
        return None
    return (path.parent / value).resolve()


def sync(conn: sqlite3.Connection, sources: list[Source], prune: bool = False) -> list[importer.ImportResult]:
    """Import every statement file under each source folder. Idempotent.

    With prune=True, statements whose source file is no longer present under any source folder
    are deleted first (their transactions go with them; manual categorisations on them are lost).
    """
    results: list[importer.ImportResult] = []
    if prune:
        present = {f.name for src in sources if src.path.exists()
                   for f in src.path.rglob("*") if f.suffix.lower() in FILE_TYPES}
        for row in conn.execute("SELECT id, source_name, account_id FROM statements").fetchall():
            base = row["source_name"].split(" [")[0]
            if base not in present:
                importer.delete_statement(conn, row["id"])
                results.append(importer.ImportResult(source=row["source_name"], status="pruned",
                                                     message="source file no longer present; statement removed"))
        for acct in conn.execute("SELECT id, name FROM accounts").fetchall():
            if importer.delete_account(conn, acct["id"]):
                results.append(importer.ImportResult(source=acct["name"], status="pruned", message="empty account removed"))
    for src in sources:
        if not src.path.exists():
            results.append(importer.ImportResult(source=str(src.path), status="error", message="folder not found"))
            continue
        files = sorted(p for p in src.path.rglob("*") if p.suffix.lower() in FILE_TYPES)
        if not files:
            results.append(importer.ImportResult(source=str(src.path), status="error", message="no PDF or CSV files"))
            continue
        spec = importer.AccountSpec(name=src.account, owner=src.owner, kind=src.kind)
        for f in files:
            for r in importer.import_file(conn, f, source_name=f.name, account=spec):
                r.source = f"{src.account}: {r.source}"
                results.append(r)
    return results
