from slopfi import db, importer, reports
from slopfi.web.app import app
from fastapi.testclient import TestClient
from factories import joint_statement


def test_net_worth_from_statements_holdings_and_snapshots(conn):
    importer.import_statement(conn, joint_statement(), "a.pdf", "h1")     # closing balance 1333.49 on 2026-07-25
    nw = reports.net_worth(conn)
    assert nw["accounts"][0]["balance"] == 1333.49 and nw["accounts"][0]["source"] == "statement"
    assert nw["total"] == 1333.49

    acct = importer.get_or_create_account(conn, "Monzo Alex", "alex", "current")
    assert any(a["balance"] is None for a in reports.account_balances(conn))
    reports.add_balance_snapshot(conn, acct, "2026-10-02", 250.0)
    reports.add_balance_snapshot(conn, acct, "2026-10-02", 300.0)    # same day: replaces
    nw = reports.net_worth(conn)
    assert {a["name"]: a["balance"] for a in nw["accounts"]}["Monzo Alex"] == 300.0

    house = reports.save_holding(conn, {"name": "Home", "kind": "property", "owner": "joint", "provider": None, "value": 400000.0,
                                        "valued_at": "2026-10-02", "rate": None, "monthly_payment": None, "fix_end": None,
                                        "term_end": None, "notes": None})
    mort = reports.save_holding(conn, {"name": "Mortgage", "kind": "mortgage", "owner": "joint", "provider": "Example Home Loans", "value": 240000.0,
                                       "valued_at": "2026-10-02", "rate": 4.5, "monthly_payment": 1500.0, "fix_end": "2027-06-30",
                                       "term_end": "2050-01-01", "notes": None})
    nw = reports.net_worth(conn)
    assert nw["equity"] == 160000.0 and abs(nw["ltv"] - 0.6) < 1e-9
    assert nw["total"] == 1333.49 + 300.0 + 400000.0 - 240000.0
    m = nw["mortgages"][0]
    assert abs(m["monthly_interest"] - 900.0) < 0.01 and abs(m["monthly_capital"] - 600.0) < 0.01

    # updating the value adds a snapshot; history is kept per date
    reports.save_holding(conn, {"name": "Mortgage", "kind": "mortgage", "owner": "joint", "provider": "Example Home Loans", "value": 239400.0,
                                "valued_at": "2026-11-02", "rate": 4.5, "monthly_payment": 1500.0, "fix_end": "2027-06-30",
                                "term_end": "2050-01-01", "notes": None}, mort)
    snaps = conn.execute("SELECT date, value FROM holding_snapshots WHERE holding_id = ? ORDER BY date", (mort,)).fetchall()
    assert [tuple(s) for s in snaps] == [("2026-10-02", 240000.0), ("2026-11-02", 239400.0)]
    reports.delete_holding(conn, house)
    assert reports.net_worth(conn)["property_value"] == 0


def test_networth_routes(db_path):
    client = TestClient(app)
    assert client.get("/networth").status_code == 200
    r = client.post("/holdings", data={"name": "Our house", "kind": "property", "owner": "joint", "value": "£425,000",
                                       "valued_at": "2026-10-02"}, follow_redirects=False)
    assert r.status_code == 303
    r = client.post("/holdings", data={"name": "Mortgage", "kind": "mortgage", "owner": "joint", "value": "250000",
                                       "valued_at": "2026-10-02", "rate": "4.2%", "monthly_payment": "1,400",
                                       "fix_end": "2028-03-31"}, follow_redirects=False)
    page = client.get("/networth").text
    assert "£175,000.00" in page and "Fix ends" in page and "2028-03-31" in page
    conn = db.connect(db_path)
    hid = conn.execute("SELECT id FROM holdings WHERE kind = 'mortgage'").fetchone()[0]
    assert "Edit Mortgage" in client.get(f"/networth?edit={hid}").text
    client.post(f"/holdings/{hid}/delete", follow_redirects=False)
    assert conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 1


def test_property_carries_its_mortgage(conn):
    base = {"name": "Home", "owner": "joint", "provider": None, "value": 300000.0, "valued_at": "2026-10-02", "notes": None}
    pid = reports.save_property(conn, base, {"value": 180000.0, "rate": 3.75, "monthly_payment": 800.0,
                                              "fix_end": "2027-07-01", "term_end": None, "provider": "Example Home Loans"})
    nw = reports.net_worth(conn)
    assert nw["mortgage"] == 180000.0 and nw["equity"] == 120000.0 and abs(nw["ltv"] - 0.6) < 1e-9
    prop = next(h for h in nw["holdings"] if h["id"] == pid)
    assert prop["mortgage"]["name"] == "Mortgage: Home" and prop["mortgage"]["parent_id"] == pid and prop["equity"] == 120000.0
    assert prop["rate"] is None   # loan terms live on the mortgage, not the property

    # update: balance changes, same linked mortgage
    reports.save_property(conn, {**base, "valued_at": "2026-11-02"}, {"value": 179500.0, "rate": 3.75, "monthly_payment": 800.0,
                                                                          "fix_end": "2027-07-01", "term_end": None, "provider": "Example Home Loans"}, pid)
    assert conn.execute("SELECT COUNT(*) FROM holdings WHERE kind = 'mortgage'").fetchone()[0] == 1
    assert reports.net_worth(conn)["mortgage"] == 179500.0
    # clearing the balance removes the mortgage; deleting the property cascades
    reports.save_property(conn, base, {"value": 0}, pid)
    assert reports.net_worth(conn)["mortgage"] == 0 and reports.net_worth(conn)["ltv"] == 0
    reports.save_property(conn, base, {"value": 1000.0}, pid)
    reports.delete_holding(conn, pid)
    assert conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 0


def test_property_form_with_mortgage(db_path):
    client = TestClient(app)
    r = client.post("/holdings", data={"name": "Our house", "kind": "property", "owner": "joint", "value": "300,000",
                                       "valued_at": "2026-10-02", "mortgage_balance": "£150,000", "rate": "3.75",
                                       "monthly_payment": "800", "fix_end": "2027-07-01"}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/networth").text
    assert "50%" in page and "Mortgage: Our house" in page and "£150,000.00" in page
