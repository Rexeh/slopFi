"""Minimal command line: import files, apply rules, run the web UI."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import categorise, db, importer, reports, rules_io


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="slopfi", description="slopFi: bank statements in, where the money goes out")
    ap.add_argument("--db", default=None, help="SQLite file (default: $SLOPFI_DB or ./slopfi.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_import = sub.add_parser("import", help="import statement files (PDF or CSV)")
    p_import.add_argument("paths", nargs="+")
    p_import.add_argument("--account", help="account name for files that don't identify their account (e.g. Monzo CSV); created if missing")
    p_import.add_argument("--owner", default="unknown",
                          help="owner key when creating the account: joint, unknown, or a person's key "
                               "from Settings > Household")
    p_import.add_argument("--kind", default="current", choices=["current", "credit_card", "savings"])

    p_sync = sub.add_parser("sync", help="import everything listed in sources.toml (safe to re-run)")
    p_sync.add_argument("--config", default=None, help="sources file (default: $SLOPFI_SOURCES or ./sources.toml)")
    p_sync.add_argument("--fresh", action="store_true",
                        help="delete the database first (loses manual categorisations and rules you added)")
    p_sync.add_argument("--prune", action="store_true",
                        help="remove statements whose source file has been deleted or replaced")

    p_rules = sub.add_parser("apply-rules", help="categorise transactions with the current rules")
    p_rules.add_argument("--recategorise", action="store_true", help="also re-evaluate rule-categorised rows")

    p_rules_io = sub.add_parser("rules", help="export or import categorisation rules as JSON")
    rules_sub = p_rules_io.add_subparsers(dest="rules_cmd", required=True)
    p_export = rules_sub.add_parser("export", help="write every rule to a JSON file")
    p_export.add_argument("file")
    p_rimport = rules_sub.add_parser("import", help="add the rules in a JSON file (duplicates are skipped)")
    p_rimport.add_argument("file")
    p_rimport.add_argument("--replace", action="store_true", help="delete every existing rule first")

    p_demo = sub.add_parser("demo", help="build a fictional demo household and its database")
    p_demo.add_argument("--out", default=None, help="directory to write into (default: ./demo)")
    p_demo.add_argument("--seed", type=int, default=42, help="random seed: the same seed builds the same household")
    p_demo.add_argument("--force", action="store_true", help="rebuild even if the demo database already exists")

    sub.add_parser("summary", help="print the monthly summary")

    p_serve = sub.add_parser("serve", help="run the web UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.add_argument("--demo", action="store_true",
                         help="serve the demo household from ./demo, building it first if it is missing")

    args = ap.parse_args(argv)

    if args.cmd == "demo":
        from .demo import build as demo_build

        out = Path(args.out) if args.out else demo_build.DEFAULT_DIR
        try:
            result = demo_build.build(out, seed=args.seed, force=args.force)
        except FileExistsError as exc:
            print(exc)
            return 1
        print(f"Built the demo household in {result.out}/ in {result.seconds:.1f}s: {len(result.files)} statement files, "
              f"{result.transactions} transactions, {result.rules} rules, {result.uncategorised} left to categorise.")
        print("Run it with:")
        print(f"  SLOPFI_DB={result.db_path} uv run slopfi serve")
        return 0

    if args.cmd == "serve":
        import os
        import uvicorn

        if args.demo:
            from .demo import build as demo_build

            demo_db = demo_build.ensure(demo_build.DEFAULT_DIR)
            os.environ["SLOPFI_DB"] = str(demo_db)
            os.environ.setdefault("SLOPFI_SOURCES", str(demo_build.DEFAULT_DIR / "sources.toml"))
            print(f"Serving the demo household from {demo_db}")
        elif args.db:
            os.environ["SLOPFI_DB"] = args.db
        uvicorn.run("slopfi.web.app:app", host=args.host, port=args.port, reload=args.reload)
        return 0

    if args.cmd == "sync" and args.fresh:
        target = Path(args.db or db.default_db_path())
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        print(f"deleted {target}")

    conn = db.connect(args.db)
    if args.cmd == "sync":
        from . import sync as sync_mod

        results = sync_mod.sync(conn, sync_mod.load_sources(args.config), prune=args.prune)
        failed = 0
        for r in results:
            extra = f"{r.inserted} new, {r.skipped} dup, {r.categorised} categorised" if r.status == "imported" else r.message
            print(f"{r.status:10} {r.source}: {extra}")
            for w in r.warnings:
                print(f"           warning: {w}")
            failed += r.status == "error"
        rules_path = sync_mod.rules_file(args.config)
        if rules_path is not None:
            if not rules_path.exists():
                print(f"error      rules_file {rules_path} not found")
                failed += 1
            else:
                try:
                    summary = rules_io.import_file(conn, rules_path)
                except ValueError as exc:
                    print(f"error      rules_file {rules_path}: {exc}")
                    failed += 1
                else:
                    n = categorise.apply_rules(conn)
                    print(f"rules      {rules_path.name}: {rules_io.describe(summary)}; {n} transactions categorised")
                    for e in summary.errors:
                        print(f"           warning: {e}")
        t = conn.execute("SELECT COUNT(*), SUM(category_id IS NULL) FROM transactions").fetchone()
        print(f"\n{t[0]} transactions in the database, {t[1] or 0} uncategorised")
        return 1 if failed else 0
    if args.cmd == "import":
        failed = 0
        account_id = importer.get_or_create_account(conn, args.account, args.owner, args.kind) if args.account else None
        for raw in args.paths:
            path = Path(raw)
            files = sorted(p for p in path.iterdir() if p.suffix.lower() in (".pdf", ".csv")) if path.is_dir() else [path]
            for f in files:
                for r in importer.import_file(conn, f, account_id=account_id):
                    print(f"{r.status:10} {r.source}: {r.inserted} new, {r.skipped} dup, {r.categorised} categorised {r.message}")
                    for w in r.warnings:
                        print(f"           warning: {w}")
                    failed += r.status == "error"
        return 1 if failed else 0
    if args.cmd == "rules":
        if args.rules_cmd == "export":
            n = rules_io.export_file(conn, args.file)
            print(f"{n} rules written to {args.file}")
            return 0
        try:
            summary = rules_io.import_file(conn, args.file, replace=args.replace)
        except (OSError, ValueError) as exc:
            print(f"not imported: {exc}")
            return 1
        n = categorise.apply_rules(conn, include_rule_categorised=args.replace)
        print(f"{rules_io.describe(summary)}; {n} transactions categorised")
        for e in summary.errors:
            print(f"  skipped {e}")
        for c in summary.categories_created:
            print(f"  created category {c}")
        return 0
    if args.cmd == "apply-rules":
        n = categorise.apply_rules(conn, include_rule_categorised=args.recategorise)
        print(f"{n} transactions categorised")
        return 0
    if args.cmd == "summary":
        print(f"{'month':8} {'outgoings':>10} {'income':>10} {'net':>10} {'xfer in':>10} {'xfer out':>10} {'uncat':>8}")
        for m in reports.monthly_summary(conn):
            print(f"{m['month']:8} {m['outgoings']:10.2f} {m['income']:10.2f} {m['net']:10.2f} "
                  f"{m['transfers_in']:10.2f} {m['transfers_out']:10.2f} {m['uncategorised_count']:8d}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
