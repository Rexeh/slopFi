"""Renewals: the register and its due window, mortgage sync, recording renewals, suggestions and the month-close step."""
from datetime import date, timedelta

import pytest

from slopfi import importer, renewals, reports, review
from factories import add_rules, rule, statement, txn

TODAY = date(2026, 11, 18)
HOUSE = {"name": "1 Example Street", "kind": "property", "owner": "joint", "provider": None, "notes": None,
         "value": 385000.0, "valued_at": "2026-10-31"}
MORTGAGE = {"provider": "Example Home Loans", "rate": 4.29, "monthly_payment": 1145.0, "value": 212390.0,
            "fix_end": "2027-03-31", "term_end": "2051-06-30"}


def _renewal(**over) -> dict:
    return {"name": "Car insurance", "kind": "insurance", "provider": "Northgate", "owner": "joint", "ends_on": "2027-06-01",
            "lead_days": None, "price": 612.0, "price_period": "year", "payee_key": None, "notes": None, **over}


def _groups(conn) -> dict[int, str]:
    return {r["id"]: r["group"] for r in renewals.register(conn, TODAY)}


# ------------------------------------------------------------------ register
def test_due_window_uses_lead_and_lookahead(conn):
    a = renewals.save_renewal(conn, _renewal(ends_on="2027-03-14"))   # reminder starts 13 Dec, within today + 35
    b = renewals.save_renewal(conn, _renewal(ends_on="2027-03-26"))   # reminder starts 25 Dec, after 23 Dec
    groups = _groups(conn)
    assert groups[a] == "due" and groups[b] == "soon"


def test_lead_days_override_and_later_group(conn):
    a = renewals.save_renewal(conn, _renewal(ends_on="2027-01-10", lead_days=30))   # starts 11 Dec: due
    b = renewals.save_renewal(conn, _renewal(ends_on="2027-02-10", lead_days=30))   # starts 11 Jan: soon
    c = renewals.save_renewal(conn, _renewal(ends_on="2027-07-01"))                  # 225 days away: later
    groups = _groups(conn)
    assert (groups[a], groups[b], groups[c]) == ("due", "soon", "later")
    assert renewals.get(conn, a, TODAY)["lead_days"] == 30 and renewals.get(conn, c, TODAY)["lead_days"] == 91


def test_register_sorted_by_group_then_end(conn):
    later = renewals.save_renewal(conn, _renewal(ends_on="2027-08-01"))
    due2 = renewals.save_renewal(conn, _renewal(ends_on="2027-01-05"))
    due1 = renewals.save_renewal(conn, _renewal(ends_on="2026-12-02"))
    assert [r["id"] for r in renewals.register(conn, TODAY)] == [due1, due2, later]
    assert [r["id"] for r in renewals.due(conn, TODAY)] == [due1, due2]


def test_ended_renewal_stays_due(conn):
    rid = renewals.save_renewal(conn, _renewal(ends_on="2026-11-01"))
    row = renewals.get(conn, rid, TODAY)
    assert row["group"] == "due" and row["days_left"] == -17


def test_annual():
    assert renewals.annual(50, "month") == 600
    assert renewals.annual(600, "year") == 600
    assert renewals.annual(55, "once") == 55
    assert renewals.annual(None, "year") is None


def test_how_paid_from_period_without_payee(conn):
    rid = renewals.save_renewal(conn, _renewal())
    assert renewals.get(conn, rid, TODAY)["how_paid"] == "paid yearly"


def test_delete_renewal(conn):
    rid = renewals.save_renewal(conn, _renewal())
    assert renewals.delete_renewal(conn, rid) is True
    assert renewals.get(conn, rid, TODAY) is None


# ------------------------------------------------------------------ mortgage
def _mortgage_row(conn):
    return next(r for r in renewals.register(conn, TODAY) if r["kind"] == "mortgage")


def test_mortgage_synced_from_holding_with_183_day_lead(conn):
    reports.save_property(conn, HOUSE, MORTGAGE)
    row = _mortgage_row(conn)
    assert row["ends_on"] == "2027-03-31" and row["lead_days"] == 183 and row["group"] == "due"
    assert (row["provider"], row["price"], row["price_period"]) == ("Example Home Loans", 1145.0, "month")
    assert renewals.delete_renewal(conn, row["id"]) is False
    assert len([r for r in renewals.register(conn, TODAY) if r["kind"] == "mortgage"]) == 1   # sync is idempotent


def test_mortgage_archived_when_fix_end_cleared_and_restored_when_set(conn):
    prop = reports.save_property(conn, HOUSE, MORTGAGE)
    rid = _mortgage_row(conn)["id"]
    reports.save_property(conn, HOUSE, {**MORTGAGE, "fix_end": None}, prop)
    assert renewals.get(conn, rid, TODAY)["group"] == "archived"
    assert renewals.due(conn, TODAY) == []
    reports.save_property(conn, HOUSE, MORTGAGE, prop)
    assert renewals.get(conn, rid, TODAY)["group"] == "due"


def test_mortgage_archived_when_holding_deleted(conn):
    prop = reports.save_property(conn, HOUSE, MORTGAGE)
    rid = _mortgage_row(conn)["id"]
    reports.delete_holding(conn, prop)
    row = renewals.get(conn, rid, TODAY)
    assert row is not None and row["group"] == "archived" and row["holding_id"] is None


# ------------------------------------------------------------------ recording
SWITCH = {"outcome": "switched", "quote": 734.0, "quote_period": "year", "other_quote": 559.0, "other_period": "year",
          "other_provider": "Kestrel Direct", "new_provider": "Kestrel Direct", "new_price": 559.0, "new_period": "year",
          "new_ends_on": "2027-12-02"}


def test_record_switch_saves_and_moves_term(conn):
    rid = renewals.save_renewal(conn, _renewal(ends_on="2026-12-02"))
    renewals.record(conn, rid, SWITCH, TODAY)
    row = renewals.get(conn, rid, TODAY)
    assert (row["provider"], row["price"], row["price_period"], row["ends_on"]) == ("Kestrel Direct", 559.0, "year", "2027-12-02")
    assert row["last"]["saved"] == 175 and row["group"] == "later"
    assert renewals.saved_in_year(conn, 2026) == 175 and renewals.saved_in_year(conn, 2025) == 0
    first = renewals.history(conn, rid)[0]
    assert first["old_provider"] == "Northgate" and first["old_price"] == 612.0 and first["saved"] == 175


def test_saving_annualises_mixed_periods():
    assert renewals.saving({"quote": 600, "quote_period": "year", "new_price": 45, "new_period": "month"}) == 60


def test_no_quote_means_no_saving(conn):
    rid = renewals.save_renewal(conn, _renewal(ends_on="2026-12-02"))
    renewals.record(conn, rid, {**SWITCH, "quote": None, "quote_period": None}, TODAY)
    assert renewals.get(conn, rid, TODAY)["last"]["saved"] is None
    assert renewals.saved_in_year(conn, 2026) == 0


def test_stayed_with_rise_reports_rise_pct(conn):
    rid = renewals.save_renewal(conn, _renewal(name="Broadband", kind="broadband", price=52.0, price_period="month",
                                               ends_on="2026-12-09"))
    renewals.record(conn, rid, {"outcome": "stayed", "new_provider": "Northgate", "new_price": 58.24, "new_period": "month",
                                "new_ends_on": "2027-12-09"}, TODAY)
    last = renewals.get(conn, rid, TODAY)["last"]
    assert last == {"outcome": "stayed", "saved": None, "rise_pct": 12}


def test_mortgage_record_writes_holding(conn):
    reports.save_property(conn, HOUSE, MORTGAGE)
    rid = _mortgage_row(conn)["id"]
    renewals.record(conn, rid, {"outcome": "switched", "quote": 1290.0, "quote_period": "month", "new_provider": "Other Bank",
                                "new_price": 1180.0, "new_period": "month", "new_ends_on": "2032-03-31", "new_rate": 3.95,
                                "new_payment": 1180.0}, TODAY)
    h = conn.execute("SELECT * FROM holdings WHERE kind = 'mortgage'").fetchone()
    assert (h["fix_end"], h["rate"], h["monthly_payment"], h["provider"]) == ("2032-03-31", 3.95, 1180.0, "Other Bank")
    row = renewals.get(conn, rid, TODAY)
    assert row["ends_on"] == "2032-03-31" and row["last"]["saved"] == 1320


def test_delete_latest_event_restores_old_term(conn):
    rid = renewals.save_renewal(conn, _renewal(ends_on="2026-12-02"))
    renewals.record(conn, rid, SWITCH, TODAY)
    assert renewals.delete_latest_event(conn, rid) is True
    row = renewals.get(conn, rid, TODAY)
    assert (row["provider"], row["price"], row["ends_on"], row["last"]) == ("Northgate", 612.0, "2026-12-02", None)


def test_delete_latest_mortgage_event_restores_holding(conn):
    reports.save_property(conn, HOUSE, MORTGAGE)
    rid = _mortgage_row(conn)["id"]
    renewals.record(conn, rid, {"outcome": "switched", "new_provider": "Other Bank", "new_price": 1180.0, "new_period": "month",
                                "new_ends_on": "2032-03-31", "new_rate": 3.95, "new_payment": 1180.0}, TODAY)
    renewals.delete_latest_event(conn, rid)
    h = conn.execute("SELECT * FROM holdings WHERE kind = 'mortgage'").fetchone()
    assert (h["fix_end"], h["rate"], h["monthly_payment"], h["provider"]) == ("2027-03-31", 4.29, 1145.0, "Example Home Loans")


def test_delete_latest_event_with_none_returns_false(conn):
    rid = renewals.save_renewal(conn, _renewal())
    assert renewals.delete_latest_event(conn, rid) is False
    assert renewals.get(conn, rid, TODAY)["provider"] == "Northgate"


def test_record_on_archived_row_raises(conn):
    prop = reports.save_property(conn, HOUSE, MORTGAGE)
    rid = _mortgage_row(conn)["id"]
    reports.delete_holding(conn, prop)
    with pytest.raises(ValueError):
        renewals.record(conn, rid, SWITCH, TODAY)


# ------------------------------------------------------------------ suggestions
def _with_direct_debits(conn):
    add_rules(conn, [rule("OCTAVIA ENERGY", "Utilities/Energy"), rule("EXAMPLE HOME LOANS", "Housing/Mortgage"),
                     rule("TESCO", "Groceries/Supermarket")])
    for n, (start, end, energy) in enumerate((("2026-06-01", "2026-06-30", -142.0), ("2026-07-01", "2026-07-31", -158.0))):
        day = start[:8] + "05"
        importer.import_statement(conn, statement([txn(day, "OCTAVIA ENERGY", energy, "DD"),
                                                   txn(day, "EXAMPLE HOME LOANS", -1145.0, "DD"),
                                                   txn(day, "TESCO STORES", -96.40, "DD")], start, end), f"{n}.pdf", f"h{n}")


def test_suggestions_filter_categories(conn):
    _with_direct_debits(conn)
    s = renewals.suggestions(conn)
    assert [x["payee_key"] for x in s] == ["OCTAVIA ENERGY"]
    assert (s[0]["name"], s[0]["provider"], s[0]["type_code"], s[0]["kind"]) == ("Energy", "Octavia Energy", "DD", "energy")


def test_linked_and_dismissed_are_hidden(conn):
    _with_direct_debits(conn)
    rid = renewals.save_renewal(conn, _renewal(name="Energy", kind="energy", payee_key="OCTAVIA ENERGY"))
    assert renewals.suggestions(conn) == []
    renewals.delete_renewal(conn, rid)
    renewals.dismiss_suggestion(conn, "OCTAVIA ENERGY")
    assert renewals.suggestions(conn) == []
    assert "OCTAVIA ENERGY" in [p["merchant"] for p in renewals.payee_choices(conn)]


def test_latest_payment_shown_when_differs(conn):
    _with_direct_debits(conn)
    rid = renewals.save_renewal(conn, _renewal(name="Energy", kind="energy", price=142.0, price_period="month",
                                               payee_key="OCTAVIA ENERGY"))
    row = renewals.get(conn, rid, TODAY)
    assert row["latest_payment"] == 158.0 and row["how_paid"] == "direct debit" and row["price"] == 142.0


# ------------------------------------------------------------------ month-close step
def _october(conn):
    """One October statement, so October 2026 is the latest open month on TODAY."""
    importer.import_statement(conn, statement([txn("2026-10-05", "TESCO STORES", -50.0, balance_after=50.0)],
                                                  "2026-10-01", "2026-10-31", opening=100.0, closing=50.0,
                                                  payments_in=0.0, payments_out=50.0), "oct.pdf", "oct")


def _step(conn, month="2026-10"):
    return next(s for s in review.checklist(conn, month, today=TODAY)["steps"] if s["key"] == "renewals")


def test_step_done_with_no_renewals(conn):
    _october(conn)
    step = _step(conn)
    assert step["done"] and step["detail"] == "No renewals recorded yet." and step["renewals"] == []


def test_step_todo_when_due_then_done_when_deferred(conn):
    _october(conn)
    rid = renewals.save_renewal(conn, _renewal(ends_on="2026-12-02"))
    renewals.save_renewal(conn, _renewal(name="Energy", ends_on="2027-08-01"))
    step = _step(conn)
    assert not step["done"] and step["count"] == "1 due"
    assert step["detail"] == "1 renewal needs a decision before the next close."
    assert [r["id"] for r in step["renewals"]] == [rid] and step["renewals"][0]["deferred"] is False
    review.defer_renewal(conn, "2026-10", rid)
    step = _step(conn)
    assert step["done"] and step["renewals"][0]["deferred"] is True
    review.undefer_renewal(conn, "2026-10", rid)
    assert not _step(conn)["done"]


def test_deferral_is_per_month(conn):
    _october(conn)
    rid = renewals.save_renewal(conn, _renewal(ends_on="2026-12-02"))
    review.defer_renewal(conn, "2026-09", rid)
    assert not _step(conn)["done"]


def test_step_done_for_month_other_than_latest_open(conn):
    _october(conn)
    importer.import_statement(conn, statement([txn("2026-09-05", "TESCO STORES", -50.0)], "2026-09-01", "2026-09-30"),
                              "sep.pdf", "sep")
    renewals.save_renewal(conn, _renewal(ends_on="2026-12-02"))
    step = _step(conn, "2026-09")
    assert step["done"] and step["detail"] == "Checked at the latest open month."


def test_close_allowed_after_deferring(conn):
    _october(conn)
    add_rules(conn, [rule("TESCO", "Groceries/Supermarket")])
    from slopfi import categorise
    categorise.apply_rules(conn)
    rid = renewals.save_renewal(conn, _renewal(ends_on="2026-12-02"))
    before = review.checklist(conn, "2026-10", today=TODAY)
    assert before["total"] == 6 and not before["can_close"]
    review.defer_renewal(conn, "2026-10", rid)
    after = review.checklist(conn, "2026-10", today=TODAY)
    assert [s["key"] for s in after["steps"] if not s["done"]] == [] and after["can_close"]


def test_defer_routes_and_review_page(db_path, monkeypatch):
    from fastapi.testclient import TestClient
    from slopfi import db
    from slopfi.web.app import app

    conn = db.connect(db_path)
    _october(conn)
    rid = renewals.save_renewal(conn, _renewal(ends_on=(date.today().replace(day=1)).isoformat()))   # ended or ending: due
    conn.close()
    month = review.latest_open_month(db.connect(db_path))
    client = TestClient(app)
    page = client.get(f"/review?month={month}").text
    assert 'id="step-renewals"' in page and "Car insurance" in page and "Look at it next close" in page
    r = client.post("/review/defer-renewal", data={"month": month, "renewal_id": rid}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#step-renewals") and "next%20close" in r.headers["set-cookie"]
    assert "Next close" in client.get(f"/review?month={month}").text
    r = client.post("/review/undefer-renewal", data={"month": month, "renewal_id": rid}, follow_redirects=False)
    assert r.status_code == 303
    assert "Look at it next close" in client.get(f"/review?month={month}").text


def test_council_tax_and_water_are_not_suggested(conn):
    add_rules(conn, [rule("EXAMPLETOWN COUNCIL", "Housing/Council tax"), rule("EXAMPLE WATER", "Utilities/Water")])
    for n, (start, end) in enumerate((("2026-06-01", "2026-06-30"), ("2026-07-01", "2026-07-31"))):
        day = start[:8] + "05"
        importer.import_statement(conn, statement([txn(day, "EXAMPLETOWN COUNCIL", -182.0, "DD"),
                                                   txn(day, "EXAMPLE WATER", -41.2, "DD")], start, end), f"c{n}.pdf", f"c{n}")
    assert renewals.suggestions(conn) == []


# ------------------------------------------------------------------ final review fixes
def test_sidebar_count_stays_when_every_month_is_closed(conn, monkeypatch):
    from slopfi.web import app as web_app
    _october(conn)
    review.close_month(conn, "2026-10")
    renewals.save_renewal(conn, _renewal(ends_on=(date.today() + timedelta(days=20)).isoformat()))
    assert web_app.Shell(conn, "/").renewals_due == 1


def test_provider_names_keep_acronyms(conn):
    add_rules(conn, [rule("DVLA", "Transport/Car insurance & tax"), rule("OCTAVIA ENERGY", "Utilities/Energy")])
    for n, (start, end) in enumerate((("2026-06-01", "2026-06-30"), ("2026-07-01", "2026-07-31"))):
        day = start[:8] + "05"
        importer.import_statement(conn, statement([txn(day, "DVLA-EX24 DMO", -16.63, "DD"),
                                                   txn(day, "OCTAVIA ENERGY", -142.0, "DD")], start, end), f"d{n}.pdf", f"d{n}")
    assert {s["provider"] for s in renewals.suggestions(conn)} == {"DVLA-EX24 DMO", "Octavia Energy"}
