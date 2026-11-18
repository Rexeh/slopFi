import sqlite3
from datetime import date

import pytest

from slopfi import db, importer, reports

from factories import ENERGY, EVERYDAY_RULES, SALARY, add_rules, statement, txn as _t


def _stmt(start, end, txns, identifier="99-10-20 12341020"):
    return statement(txns, start, end, identifier=identifier)


@pytest.fixture
def trend_db(conn):
    # Jan–Jun 2026, one account fully covered; groceries steady, eating out rising, one car-repair spike
    add_rules(conn, EVERYDAY_RULES)
    txns = []
    for i, m in enumerate(["01", "02", "03", "04", "05", "06"], start=1):
        txns += [
            _t(f"2026-{m}-05", "TESCO STORES", -300.0),
            _t(f"2026-{m}-10", "DELIVEROO", -(50.0 + 10 * i)),
            _t(f"2026-{m}-01", ENERGY, -100.0, "DD"),
            _t(f"2026-{m}-28", SALARY, 3000.0, "BACS"),
        ]
    txns.append(_t("2026-05-15", "MOTORCARE EXAMPLETOWN", -900.0, "VIS"))
    importer.import_statement(conn, _stmt("2026-01-01", "2026-06-30", txns), "a.pdf", "h1")
    # second account covering only Apr–Jun
    importer.import_statement(conn, _stmt("2026-04-01", "2026-06-30", [_t("2026-04-02", "NETFLIX.COM", -13.0, "VIS")],
                                          identifier="99-10-21 12341021"), "b.pdf", "h2")
    return conn


def test_schema_version_and_migration(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    old_schema = db.SCHEMA.replace(",\n    one_off        INTEGER NOT NULL DEFAULT 0", "")
    assert "one_off" not in old_schema
    raw.executescript(old_schema)
    raw.execute("PRAGMA user_version = 1")
    raw.commit(); raw.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)")]
    assert "one_off" in cols
    assert conn.execute("SELECT COUNT(*) FROM budgets").fetchone()[0] == 0
    conn.close()

    # an older build re-stamped version 1 after the column was added: migration must be a no-op, not a crash
    raw = sqlite3.connect(path); raw.execute("PRAGMA user_version = 1"); raw.commit(); raw.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    conn.close()

    # a database newer than the code is refused rather than downgraded
    raw = sqlite3.connect(path); raw.execute("PRAGMA user_version = 99"); raw.commit(); raw.close()
    with pytest.raises(RuntimeError):
        db.connect(path)


def test_month_coverage(trend_db):
    cov = {c["month"]: c for c in reports.month_coverage(trend_db)}
    assert cov["2026-01"]["covered"] is False and cov["2026-01"]["missing"]   # second account absent
    assert cov["2026-04"]["complete"] and cov["2026-06"]["complete"]
    months, _ = reports.trend_months(trend_db, 3)
    assert months == ["2026-04", "2026-05", "2026-06"]
    months, _ = reports.trend_months(trend_db, 12)
    assert months == ["2026-04", "2026-05", "2026-06"]


def test_trend_table_and_one_off(trend_db):
    months = ["2026-04", "2026-05", "2026-06"]
    table = reports.trend_table(trend_db, months)
    cats = {c["name"]: c for c in table["categories"]}
    assert cats["Groceries"]["avg"] == 300.0 and cats["Groceries"]["change"] == 0.0
    assert cats["Eating out"]["series"] == [90.0, 100.0, 110.0]
    assert cats["Eating out"]["prev_avg"] == 70.0 and cats["Eating out"]["change"] == 30.0
    energy = next(ch for ch in cats["Utilities"]["children"] if ch["name"] == "Energy")
    assert energy["fixed_share"] == 1.0 and 0.9 < cats["Utilities"]["fixed_share"] < 1.0   # Netflix is a card payment
    assert cats["Transport"]["lumpy"] and cats["Transport"]["max"] == 900.0
    assert table["prev_months"] == ["2026-01", "2026-02", "2026-03"]
    assert table["totals"]["avg_income"] == 3000.0

    txn = trend_db.execute("SELECT id FROM transactions WHERE description LIKE 'MOTORCARE%'").fetchone()[0]
    reports.set_one_off(trend_db, txn, True)
    table = reports.trend_table(trend_db, months)
    assert "Transport" not in {c["name"] for c in table["categories"]}
    table = reports.trend_table(trend_db, months, exclude_one_off=False)
    assert "Transport" in {c["name"] for c in table["categories"]}


def test_spread_category(trend_db):
    from slopfi import overview, review
    months = ["2026-04", "2026-05", "2026-06"]
    importer.import_statement(trend_db, _stmt("2026-04-01", "2026-04-30", [_t("2026-04-20", "MOTORCARE EXAMPLETOWN", -50.0, "VIS")],
                                              identifier="99-10-22 12341022"), "c.pdf", "h3")
    assert any(s["name"] == "Transport" for s in review._spikes(trend_db, "2026-05"))   # 900 against 50/3
    trend_db.execute("UPDATE categories SET spread = 1 WHERE id = ?", (db.category_id_by_path(trend_db, "Transport/Car maintenance"),))

    # 50 in April and 900 in May, averaged over the months with data in the 12 ending each: Jan–Apr (4), –May (5), –Jun (6)
    cats = {c["name"]: c for c in reports.trend_table(trend_db, months)["categories"]}
    assert cats["Transport"]["series"] == pytest.approx([12.5, 190.0, 950 / 6]) and not cats["Transport"]["lumpy"]
    actual = {m["month"]: m["outgoings"] for m in reports.monthly_summary(trend_db)}
    spread = {m["month"]: m["outgoings"] for m in reports.monthly_summary(trend_db, spread=True)}
    assert spread["2026-05"] == pytest.approx(actual["2026-05"] - 900 + 190)
    assert spread["2026-06"] == pytest.approx(actual["2026-06"] + 950 / 6)
    assert not any(s["name"] == "Transport" for s in review._spikes(trend_db, "2026-05"))
    # the Overview: the month itself is actual spend, typical (Mar–May for June) is the spread baseline
    against = overview.against_typical(trend_db, "2026-06")
    transport = next(r for r in against["rows"] if r["name"] == "Transport")
    assert transport["spend"] == 0.0 and transport["typical"] == pytest.approx((0 + 12.5 + 190) / 3)


def test_overview_typical_leaves_out_one_offs(trend_db):
    from slopfi import overview
    txn = trend_db.execute("SELECT id FROM transactions WHERE description LIKE 'MOTORCARE%'").fetchone()[0]
    before = overview.typical_summary(trend_db, "2026-06")["spending"]
    reports.set_one_off(trend_db, txn, True)
    assert overview.typical_summary(trend_db, "2026-06")["spending"] == pytest.approx(before - 300)   # 900 over 3 months
    may = next(r for r in overview.six_months(trend_db, today=date(2026, 7, 15)) if r["month"] == "2026-05")
    assert may["outgoings"] == next(m["outgoings"] for m in reports.monthly_summary(trend_db) if m["month"] == "2026-05")


def test_budgets_and_opportunities(trend_db):
    months = ["2026-04", "2026-05", "2026-06"]
    eating = db.category_id_by_path(trend_db, "Eating out")
    reports.set_budget(trend_db, eating, 80.0)
    table = reports.trend_table(trend_db, months)
    c = next(c for c in table["categories"] if c["name"] == "Eating out")
    assert c["target"] == 80.0 and c["gap"] == 20.0
    assert table["target_total"] == 80.0
    opps = reports.savings_opportunities(trend_db, months, table)
    kinds = {o["kind"] for o in opps}
    assert {"discretionary", "lumpy", "fixed"} <= kinds
    assert opps[0]["saving"] >= opps[-1]["saving"]
    reports.set_budget(trend_db, eating, None)
    assert reports.budgets(trend_db) == {}
