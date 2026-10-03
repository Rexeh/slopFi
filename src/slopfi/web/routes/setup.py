"""Setup: accounts and statements, categories and rules."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ... import categorise, household, importer, reports, rules_io
from ...review import date_label
from ..app import Conn, FieldError, _num, hx_events, redirect, render

router = APIRouter()
RULES_URL = "/categories#rules"

# Display names: no raw enum reaches a template. Owners come from the household (Settings > Household).
ACCOUNT_KINDS = {"current": "Current account", "credit_card": "Credit card", "savings": "Savings"}
CATEGORY_KINDS = {"expense": "Spending", "income": "Income", "transfer": "Transfer"}
MATCH_TYPES = {"contains": "Contains", "prefix": "Starts with", "regex": "Expression"}
RULE_SOURCES = {"manual": "Manual", "seed": "Built in", "bank": "From bank", "import": "Imported"}
STATEMENT_STATUS = {"imported": "Imported", "duplicate": "Already imported", "error": "Not imported", "pruned": "Removed"}


def _label(table: dict[str, str], value: str | None) -> str:
    return table.get(value or "", (value or "").replace("_", " ").capitalize())


def _plural(n: int, noun: str) -> str:
    if n == 1:
        return f"1 {noun}"
    return f"{n} {noun[:-1] + 'ies' if noun.endswith('y') else noun + 's'}"


# --------------------------------------------------------------- statements
def _prunable(conn: sqlite3.Connection) -> dict:
    """What "Remove statements whose files are gone" would delete, for the confirmation."""
    from ... import sync as sync_mod

    try:
        sources = sync_mod.load_sources()
    except (FileNotFoundError, ValueError):
        return {"statements": 0, "transactions": 0, "names": []}
    present = {f.name for src in sources if src.path.exists()
               for f in src.path.rglob("*") if f.suffix.lower() in sync_mod.FILE_TYPES}
    gone = [r for r in conn.execute("SELECT source_name, transaction_count FROM statements")
            if r["source_name"].split(" [")[0] not in present]
    return {"statements": len(gone), "transactions": sum(r["transaction_count"] for r in gone),
            "names": [r["source_name"] for r in gone]}


def _account_rows(conn: sqlite3.Connection) -> list[dict]:
    out = []
    owners = household.owner_choices(conn)
    for a in reports.accounts(conn):
        d = dict(a)
        d["owner_label"] = household.owner_label(owners, a["owner"])
        d["kind_label"] = _label(ACCOUNT_KINDS, a["kind"])
        d["range"] = f"{date_label(a['first_date'])} to {date_label(a['last_date'])}" if a["first_date"] else ""
        d["n_statements"] = conn.execute("SELECT COUNT(*) FROM statements WHERE account_id = ?", (a["id"],)).fetchone()[0]
        out.append(d)
    return out


def _statement_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT s.*, a.name AS account_name FROM statements s JOIN accounts a ON a.id = s.account_id
           ORDER BY s.period_end DESC, s.account_id"""
    ).fetchall()
    return [dict(r, period=f"{date_label(r['period_start'])} to {date_label(r['period_end'])}") for r in rows]


def _statements_page(request: Request, conn: sqlite3.Connection, results=None, status: int = 200):
    accounts = _account_rows(conn)
    statements = _statement_rows(conn)
    latest = max((s["period_end"] for s in statements), default=None)
    return render(request, "statements.html", status_code=status, statements=statements, results=results or [],
                  accounts=accounts, owners=household.owner_choices(conn), kinds=ACCOUNT_KINDS, statuses=STATEMENT_STATUS,
                  prunable=_prunable(conn), latest=date_label(latest) if latest else None)


@router.get("/statements", response_class=HTMLResponse)
def statements(request: Request, conn: Conn):
    return _statements_page(request, conn)


@router.post("/statements/upload", response_class=HTMLResponse)
async def upload_statements(
    request: Request, conn: Conn, files: list[UploadFile] = File(...),
    account_id: str = Form(""), new_account_name: str = Form(""), new_account_owner: str = Form("unknown"),
    new_account_kind: str = Form("current"),
):
    if new_account_owner not in household.owner_choices(conn):
        new_account_owner = "unknown"
    if account_id == "new" and new_account_name.strip():
        acct: int | None = importer.get_or_create_account(conn, new_account_name.strip(), new_account_owner, new_account_kind)
    else:
        acct = int(account_id) if account_id.isdigit() else None
    results = []
    for up in files:
        data = await up.read()
        with tempfile.NamedTemporaryFile(suffix=Path(up.filename or "upload.pdf").suffix, delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            results.extend(importer.import_file(conn, tmp.name, source_name=up.filename or "upload", account_id=acct))
    return _statements_page(request, conn, results)


@router.post("/statements/sync", response_class=HTMLResponse)
def sync_statements(request: Request, conn: Conn, prune: str = Form("")):
    from ... import sync as sync_mod

    try:
        results = sync_mod.sync(conn, sync_mod.load_sources(), prune=prune == "1")
    except (FileNotFoundError, ValueError) as exc:
        results = [importer.ImportResult(source="Configured folders", status="error", message=str(exc))]
    return _statements_page(request, conn, results)


@router.post("/statements/{statement_id}/delete")
def delete_statement(conn: Conn, statement_id: int):
    row = conn.execute("SELECT source_name, transaction_count FROM statements WHERE id = ?", (statement_id,)).fetchone()
    if row is None:
        return redirect("/statements", "That statement was already deleted", kind="info")
    importer.delete_statement(conn, statement_id)
    return redirect("/statements", f"{row['source_name']} deleted, {_plural(row['transaction_count'], 'transaction')} removed")


@router.post("/accounts/{account_id}/delete")
def delete_account(conn: Conn, account_id: int):
    row = conn.execute("SELECT name FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        return redirect("/statements#accounts", "That account was already deleted", kind="info")
    if not importer.delete_account(conn, account_id):
        return redirect("/statements#accounts", f"{row['name']} still has statements, so it was not deleted", kind="error")
    return redirect("/statements#accounts", f"{row['name']} deleted")


@router.post("/accounts/{account_id}")
def update_account(conn: Conn, account_id: int, name: str = Form(...), owner: str = Form(...), kind: str = Form(...)):
    name = name.strip()
    if not name or owner not in household.owner_choices(conn) or kind not in ACCOUNT_KINDS:
        return redirect("/statements#accounts", "Account not saved: give it a name, an owner and a type", kind="error")
    try:
        with conn:
            conn.execute("UPDATE accounts SET name = ?, owner = ?, kind = ? WHERE id = ?", (name, owner, kind, account_id))
    except sqlite3.IntegrityError:
        return redirect("/statements#accounts", f"Account not saved: another account is already called {name}", kind="error")
    return redirect("/statements#accounts", f"{name} saved")


# --------------------------------------------------------------- categories
def _rule_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["match_label"] = _label(MATCH_TYPES, r["match_type"])
    d["source_label"] = _label(RULE_SOURCES, r["source"])
    lo, hi = r["amount_min"], r["amount_max"]
    if lo is not None and hi is not None:
        d["amount_label"] = f"{reports.money_whole(lo)} to {reports.money_whole(hi)}"
    elif lo is not None:
        d["amount_label"] = f"from {reports.money_whole(lo)}"
    elif hi is not None:
        d["amount_label"] = f"up to {reports.money_whole(hi)}"
    else:
        d["amount_label"] = ""
    return d


def _rules(conn: sqlite3.Connection, rule_id: int | None = None) -> list[dict]:
    where, params = ("WHERE r.id = ?", [rule_id]) if rule_id is not None else ("", [])
    rows = conn.execute(
        f"""SELECT r.*, COALESCE(p.name || ' / ' || c.name, c.name) AS category,
                   (SELECT COUNT(*) FROM transactions t WHERE t.rule_id = r.id) AS n_txn
            FROM rules r JOIN categories c ON c.id = r.category_id LEFT JOIN categories p ON p.id = c.parent_id
            {where} ORDER BY r.priority, r.id""", params,
    ).fetchall()
    return [_rule_row(r) for r in rows]


def _tree(conn: sqlite3.Connection) -> list[dict]:
    tree = reports.categories_tree(conn)
    for t in tree:
        t["kind_label"] = _label(CATEGORY_KINDS, t["kind"])
        t["path"] = t["name"]
        t["in_use"] = _in_use(t, children=len(t["children"]))
        for c in t["children"]:
            c["kind_label"] = t["kind_label"]
            c["path"] = f"{t['name']} / {c['name']}"
            c["in_use"] = _in_use(c)
    return tree


def _in_use(cat: dict, children: int = 0) -> str:
    """Why a category cannot be deleted, or '' when it can."""
    parts = []
    if cat["n_txn"]:
        parts.append(_plural(cat["n_txn"], "transaction"))
    if cat["n_rules"]:
        parts.append(_plural(cat["n_rules"], "rule"))
    if children:
        parts.append(_plural(children, "sub-category"))
    return ", ".join(parts)


def _categories_page(request: Request, conn: sqlite3.Connection, errors=None, form=None, status=200, tab="categories"):
    rules = _rules(conn)
    uncategorised = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_id IS NULL").fetchone()[0]
    return render(request, "categories.html", status_code=status, tree=_tree(conn), rules=rules,
                  categories=reports.category_options(conn), errors=errors or {}, form=form or {},
                  category_kinds=CATEGORY_KINDS, match_types=MATCH_TYPES, uncategorised=uncategorised,
                  n_disabled=sum(1 for r in rules if not r["enabled"]), tab=tab)


@router.get("/categories", response_class=HTMLResponse)
def categories(request: Request, conn: Conn):
    return _categories_page(request, conn)


@router.post("/categories")
def add_category(conn: Conn, name: str = Form(...), kind: str = Form("expense"), parent_id: str = Form("")):
    name = name.strip()
    if not name:
        return redirect("/categories", "Category not added: give it a name", kind="error")
    parent = int(parent_id) if parent_id else None
    if parent is not None:
        row = conn.execute("SELECT kind FROM categories WHERE id = ?", (parent,)).fetchone()
        if row is None:
            return redirect("/categories", "Category not added: that parent no longer exists", kind="error")
        kind = row["kind"]
    if kind not in CATEGORY_KINDS:
        kind = "expense"
    with conn:
        cur = conn.execute("INSERT OR IGNORE INTO categories (name, parent_id, kind, sort_order) VALUES (?, ?, ?, 99)",
                           (name, parent, kind))
    if not cur.rowcount:
        return redirect("/categories", f"{name} already exists", kind="info")
    return redirect("/categories", f"{name} added")


@router.post("/categories/{cat_id}/rename")
def rename_category(conn: Conn, cat_id: int, name: str = Form(...)):
    name = name.strip()
    if not name:
        return redirect("/categories", "Category not renamed: give it a name", kind="error")
    try:
        with conn:
            conn.execute("UPDATE categories SET name = ? WHERE id = ?", (name, cat_id))
    except sqlite3.IntegrityError:
        return redirect("/categories", f"Category not renamed: {name} already exists here", kind="error")
    return redirect("/categories", f"Renamed to {name}")


@router.post("/categories/{cat_id}/delete")
def delete_category(conn: Conn, cat_id: int):
    row = conn.execute(
        """SELECT c.name, (SELECT COUNT(*) FROM transactions t WHERE t.category_id = c.id) AS n_txn,
                  (SELECT COUNT(*) FROM rules r WHERE r.category_id = c.id) AS n_rules,
                  (SELECT COUNT(*) FROM categories k WHERE k.parent_id = c.id) AS n_children
           FROM categories c WHERE c.id = ?""", (cat_id,)).fetchone()
    if row is None:
        return redirect("/categories", "That category was already deleted", kind="info")
    reason = _in_use(row, children=row["n_children"])
    if reason:
        return redirect("/categories", f"{row['name']} is in use ({reason}), so it was not deleted", kind="error")
    with conn:
        conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
    return redirect("/categories", f"{row['name']} deleted")


# -------------------------------------------------------------------- rules
@router.get("/rules")
def rules_redirect():
    """Rules now live on the Categories page."""
    return RedirectResponse(RULES_URL, status_code=308)


@router.post("/rules")
def add_rule(
    request: Request, conn: Conn, pattern: str = Form(...), category_id: int = Form(...), match_type: str = Form("contains"),
    type_code: str = Form(""), amount_min: str = Form(""), amount_max: str = Form(""), priority: str = Form("50"),
):
    form = {"pattern": pattern, "category_id": category_id, "match_type": match_type, "type_code": type_code,
            "amount_min": amount_min, "amount_max": amount_max, "priority": priority}
    try:
        lo, hi = _num(amount_min, "amount_min"), _num(amount_max, "amount_max")
        pri = _num(priority, "priority")
    except FieldError as exc:
        return _categories_page(request, conn, errors={exc.field: exc.message}, status=400, form=form, tab="rules")
    if match_type not in MATCH_TYPES:
        match_type = "contains"
    try:
        categorise.create_rule(conn, pattern=pattern, category_id=category_id, match_type=match_type,
                               type_code=type_code.strip() or None, amount_min=lo, amount_max=hi,
                               priority=int(pri) if pri is not None else 50)
    except ValueError:
        return _categories_page(request, conn, errors={"pattern": "Enter a pattern."}, status=400, form=form, tab="rules")
    except Exception:  # noqa: BLE001  (a regex that does not compile)
        return _categories_page(request, conn, errors={"pattern": "That expression doesn't compile. Check the brackets."},
                                status=400, form=form, tab="rules")
    n = categorise.apply_rules(conn)
    return redirect(RULES_URL, f"Rule added, {_plural(n, 'transaction')} filed")


@router.post("/rules/{rule_id}/delete")
def delete_rule(conn: Conn, rule_id: int):
    row = conn.execute("SELECT pattern FROM rules WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        return redirect(RULES_URL, "That rule was already deleted", kind="info")
    with conn:
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    return redirect(RULES_URL, f"Rule {row['pattern']} deleted")


@router.post("/rules/{rule_id}/toggle")
def toggle_rule(request: Request, conn: Conn, rule_id: int):
    with conn:
        conn.execute("UPDATE rules SET enabled = 1 - enabled WHERE id = ?", (rule_id,))
    rows = _rules(conn, rule_id)
    if not rows:
        return redirect(RULES_URL, "That rule no longer exists", kind="error")
    r = rows[0]
    message = f"Rule {r['pattern']} {'enabled' if r['enabled'] else 'disabled'}"
    if request.headers.get("HX-Request"):
        return hx_events(render(request, "_rule_row.html", r=r), toast=message)
    return redirect(RULES_URL, message)


@router.post("/rules/apply")
def apply_rules(conn: Conn, recategorise: str = Form("")):
    n = categorise.apply_rules(conn, include_rule_categorised=bool(recategorise))
    return redirect(RULES_URL, f"{_plural(n, 'transaction')} filed by rules")


RULES_FILENAME = "slopfi-rules.json"


@router.get("/rules/export")
def export_rules(conn: Conn):
    """Every rule as a JSON file, in the format `slopfi rules import` and Import rules read."""
    body = rules_io.dumps(rules_io.export_rules(conn))
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{RULES_FILENAME}"'})


@router.post("/rules/import")
async def import_rules(conn: Conn, file: UploadFile = File(...), replace: str = Form("")):
    raw = await file.read()
    try:
        rules = rules_io.parse(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        reason = exc.args[0] if isinstance(exc, ValueError) and exc.args else "it is not a text file"
        return redirect(RULES_URL, f"Rules not imported: {file.filename or 'that file'} is not a rules file ({reason})",
                        kind="error")
    summary = rules_io.import_rules(conn, rules, replace=replace == "1")
    filed = categorise.apply_rules(conn, include_rule_categorised=replace == "1")
    message = f"{rules_io.describe(summary)}, {_plural(filed, 'transaction')} filed"
    return redirect(RULES_URL, message[0].upper() + message[1:], kind="error" if summary.errors and not summary.added else
                    ("info" if summary.errors else "success"))
