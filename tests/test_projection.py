from datetime import date

from fastapi.testclient import TestClient

from slopfi import db, importer, projection, reports
from slopfi.projection import Inputs, Item, run
from slopfi.web.app import app

from factories import ENERGY, EVERYDAY_RULES, SALARY, add_rules


def test_engine_single_asset_compounds_monthly_with_contributions():
    inp = Inputs(items=[Item("a", "cash", "cash", 1000.0, 12.0, contribution=100.0)], start=date(2026, 10, 1),
                 horizon_years=1, inflation_pct=0.0, property_growth_pct=0.0)
    r = run(inp)
    # 1% per month on balance, then +100: after 1 month 1110; after 12 months 1000·1.01^12 + 100·(1.01^12−1)/0.01 = 2395.08
    assert abs(r["rows"][1]["net_worth"] - 1110.0) < 1e-9
    assert abs(r["rows"][12]["net_worth"] - 2395.08) < 0.01
    assert abs(r["rows"][12]["contributions"] - 1200.0) < 1e-9
    assert abs(r["rows"][12]["growth"] - (2395.08 - 1000 - 1200)) < 0.01
    assert r["milestones"][0]["month"] == 12 and r["start_net_worth"] == 1000.0


def test_engine_mortgage_amortises_and_pays_off():
    m = Item("m", "mortgage", "mortgage", 10000.0, 12.0, is_liability=True, payment=800.0, overpayment=200.0)
    house = Item("h", "house", "property", 100000.0, 0.0)
    inp = Inputs(items=[house, m], start=date(2026, 10, 1), horizon_years=3, inflation_pct=0.0, property_growth_pct=0.0)
    r = run(inp)
    # month 1: interest 100, capital 900 -> 9100
    assert abs(r["rows"][1]["mortgage"] - 9100.0) < 1e-9
    assert r["payoff"]["m"] == 11  # 10000 at 1%/month with 1000/month clears in the 11th month
    assert r["rows"][12]["mortgage"] == 0.0 and r["rows"][12]["equity"] == 100000.0
    assert r["rows"][12]["net_worth"] == 100000.0


def test_engine_real_terms_and_property_growth_and_until():
    items = [Item("h", "house", "property", 100000.0, 0.0),
             Item("s", "stocks", "stocks", 0.0, 0.0, contribution=100.0, until=date(2027, 3, 1))]
    inp = Inputs(items=items, start=date(2026, 10, 1), horizon_years=1, inflation_pct=10.0, property_growth_pct=10.0)
    r = run(inp)
    assert abs(r["rows"][12]["balances"]["h"] - 110000.0) < 0.01          # (1.1)^(12/12)
    assert abs(r["rows"][12]["net_worth_real"] - r["rows"][12]["net_worth"] / 1.1) < 1e-6
    assert r["rows"][12]["balances"]["s"] == 500.0                        # Nov..Mar = 5 contributions


def test_observed_and_build_inputs(conn, tmp_path):
    from factories import MONZO_CSV_HEADER as HEADER
    # a pot with deposits and interest, so observed contribution (median) and rate are derived
    rows = (
        "p1,01/05/2026,01:00:00,Pot transfer, Pot,,Savings,500.00,GBP,500.00,GBP,,,,,,,500.00\n"
        "p2,01/06/2026,01:00:00,Pot transfer, Pot,,Savings,300.00,GBP,300.00,GBP,,,,,,,300.00\n"
        "p3,01/07/2026,01:00:00,Pot transfer, Pot,,Savings,-1000.00,GBP,-1000.00,GBP,,,,,,-1000.00,\n"
        "p4,01/07/2026,02:00:00,pot-savings,,,Savings,10.00,GBP,10.00,GBP,,,,Interest for June 2026,,,10.00\n"
    )
    d = tmp_path
    (d / "Monzo_pot.csv").write_text(HEADER + rows, encoding="utf-8")
    acct = importer.get_or_create_account(conn, "Pot", "alex", "savings")
    importer.import_file(conn, d / "Monzo_pot.csv", account_id=acct)
    with conn:
        conn.execute("UPDATE transactions SET balance_after = 4010 WHERE type_code = 'INT'")
    reports.add_balance_snapshot(conn, acct, "2026-10-01", 4010.0)
    assert projection.observed_contributions(conn)[("account", acct)] == 400.0   # May and June are complete: (500 + 300) / 2; July is partial
    assert projection.observed_rates(conn)[("account", acct)] == 3.0            # 10 / 4000 * 12
    reports.save_property(conn, {"name": "Home", "owner": "joint", "provider": None, "value": 300000.0, "valued_at": "2026-10-01", "notes": None},
                          {"value": 120000.0, "rate": 3.75, "monthly_payment": 845.0, "fix_end": None, "term_end": None, "provider": None})
    sid = projection.ensure_default_scenario(conn)
    inp, meta = projection.build_inputs(conn, sid, start=date(2026, 10, 1))
    by = {it.name: it for it in inp.items}
    assert by["Pot"].rate == 3.0 and by["Pot"].rate_source == "observed" and by["Pot"].contribution == 400.0
    assert by["Mortgage: Home"].is_liability and by["Mortgage: Home"].payment == 845.0 and by["Mortgage: Home"].rate == 3.75
    assert inp.inflation_pct == 3.1 and inp.horizon_years == 10
    r = run(inp)
    assert r["rows"][0]["net_worth"] == 4010.0 + 300000.0 - 120000.0

    # overrides and settings flow through
    projection.save_assumptions(conn, sid, {"inflation_pct": 2.0, "property_growth_pct": 0.0, "mortgage_overpayment": 300.0,
                                            "horizon_years": 5, "contribution_growth_pct": 0},
                                [{"ref_type": "account", "ref_id": acct, "annual_growth_pct": 4.0, "monthly_contribution": 250.0}])
    inp, meta = projection.build_inputs(conn, sid, start=date(2026, 10, 1))
    by = {it.name: it for it in inp.items}
    assert by["Pot"].rate == 4.0 and by["Pot"].contribution == 250.0 and by["Mortgage: Home"].overpayment == 300.0
    assert inp.inflation_pct == 2.0 and inp.horizon_years == 5
    projection.save_settings(conn, {"proj.rate.stocks": "7"})
    assert projection.setting(conn, "proj.rate.stocks") == 7.0
    projection.save_settings(conn, {"proj.rate.stocks": ""})
    assert projection.setting(conn, "proj.rate.stocks") == 5.0

    new = projection.clone_scenario(conn, sid, "Stretch")
    assert len(projection.scenarios(conn)) == 2
    assert projection.delete_scenario(conn, sid) is False and projection.delete_scenario(conn, new) is True


def test_projection_and_settings_routes(db_path):
    client = TestClient(app)
    assert client.get("/projection").status_code == 200
    assert client.get("/settings").status_code == 200
    r = client.post("/settings", data={"proj.inflation": "2.5", "proj.rate.stocks": "6"}, follow_redirects=False)
    assert r.status_code == 303
    conn = db.connect(db_path)
    assert projection.setting(conn, "proj.inflation") == 2.5
    sid = projection.ensure_default_scenario(conn)
    conn.close()
    r = client.post(f"/projection/{sid}/clone", data={"name": "Optimistic"}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/projection?compare=" + r.headers["location"].split("=")[1]).text
    assert "Optimistic" in page and "In 1 year" in page


def test_levers_and_surplus_flow_into_projection(conn):
    from test_trends import _stmt, _t
    # six months of steady cash flow: 3000 in, 300 groceries + 160 eating out + 100 energy out
    add_rules(conn, EVERYDAY_RULES)
    txns = []
    for m in ["04", "05", "06", "07", "08", "09"]:
        txns += [_t(f"2026-{m}-05", "TESCO STORES", -300.0), _t(f"2026-{m}-10", "DELIVEROO", -160.0),
                 _t(f"2026-{m}-01", ENERGY, -100.0, "DD"), _t(f"2026-{m}-28", SALARY, 3000.0, "BACS")]
    importer.import_statement(conn, _stmt("2026-04-01", "2026-09-30", txns), "a.pdf", "h1")
    cf = projection.cash_flow(conn)
    assert cf["months"] == ["2026-07", "2026-08", "2026-09"] and cf["income"] == 3000.0 and cf["outgoings"] == 560.0
    cats = {c["name"]: c for c in cf["categories"]}
    assert cats["Groceries"]["avg"] == 300.0 and cats["Eating out"]["avg"] == 160.0

    sid = projection.ensure_default_scenario(conn)
    inp, meta = projection.build_inputs(conn, sid, start=date(2026, 10, 1))
    assert meta["_levers"]["extra_monthly"] == 0.0 and not any(it.key == "surplus" for it in inp.items)   # ignored by default

    live = {"category_savings": {str(cats["Eating out"]["id"]): 25}, "surplus_mode": "save", "surplus_override": None}
    inp, meta = projection.build_inputs(conn, sid, start=date(2026, 10, 1), live=live)
    lev = meta["_levers"]
    assert lev["saved_total"] == 40.0 and lev["derived_surplus"] == 2440.0 and lev["extra_monthly"] == 2480.0
    surplus = next(it for it in inp.items if it.key == "surplus")
    assert surplus.contribution == 2480.0 and surplus.rate == 4.0
    r = run(inp)
    assert abs(r["rows"][12]["contributions"] - 12 * 2480.0) < 1e-6

    # persisted levers are used when no live values are given; override surplus to a fixed figure
    projection.save_levers(conn, sid, "save", 500.0, None, {cats["Groceries"]["id"]: 10})
    inp, meta = projection.build_inputs(conn, sid, start=date(2026, 10, 1))
    assert meta["_levers"]["saved_total"] == 30.0 and meta["_levers"]["surplus_used"] == 500.0 and meta["_levers"]["extra_monthly"] == 530.0


def test_projection_api_and_levers_route(db_path):
    from test_trends import _stmt, _t
    conn = db.connect(db_path)
    add_rules(conn, EVERYDAY_RULES)
    txns = []
    for m in ["07", "08", "09"]:
        txns += [_t(f"2026-{m}-05", "TESCO STORES", -300.0), _t(f"2026-{m}-28", SALARY, 2000.0, "BACS")]
    importer.import_statement(conn, _stmt("2026-07-01", "2026-09-30", txns), "a.pdf", "h1")
    groceries = db.category_id_by_path(conn, "Groceries")
    sid = projection.ensure_default_scenario(conn)
    conn.close()
    client = TestClient(app)
    page = client.get("/projection").text
    assert "Spending levers" in page and 'name="save__' in page
    r = client.get(f"/api/projection?scenario={sid}&savings={groceries}:20&surplus_mode=save")
    assert r.status_code == 200
    d = r.json()
    assert d["levers"]["saved_total"] == 60.0 and d["levers"]["surplus_used"] == 1700.0 and d["levers"]["extra_monthly"] == 1760.0
    assert len(d["milestones"]) == 4 and d["milestones"][0]["contributions"] == 12 * 1760.0
    r = client.post(f"/projection/{sid}/levers", data={f"save__{groceries}": "20", "surplus_mode": "save", "surplus_override": "", "surplus_target": ""}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/projection").text
    assert "£1,760.00" in page


def test_negative_cash_balance_earns_nothing():
    inp = Inputs(items=[Item("c", "cash", "cash", 100.0, 12.0, contribution=-200.0)], start=date(2026, 10, 1),
                 horizon_years=1, inflation_pct=0.0, property_growth_pct=0.0)
    r = run(inp)
    assert r["rows"][1]["net_worth"] == 100.0 * 1.01 - 200.0      # month 1: interest on the positive balance, then the drain
    assert r["rows"][2]["net_worth"] == r["rows"][1]["net_worth"] - 200.0   # no interest on a negative balance


def test_one_off_income_leaves_the_trend_totals(conn):
    from test_trends import _stmt, _t
    txns = []
    for m in ["07", "08", "09"]:
        txns += [_t(f"2026-{m}-05", "TESCO STORES", -300.0), _t(f"2026-{m}-28", SALARY, 3000.0, "BACS")]
    txns.append(_t("2026-09-29", "ACME ANALYTICS LTD / BONUS", 9000.0, "BACS"))
    importer.import_statement(conn, _stmt("2026-07-01", "2026-09-30", txns), "a.pdf", "h1")
    assert projection.cash_flow(conn)["income"] == 6000.0
    bonus = conn.execute("SELECT id FROM transactions WHERE description LIKE '%BONUS%'").fetchone()[0]
    reports.set_one_off(conn, bonus, True)
    assert projection.cash_flow(conn)["income"] == 3000.0
