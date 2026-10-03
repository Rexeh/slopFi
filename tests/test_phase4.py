"""Phase 4 (Projection): stacked bands, scenario menu outside the filter form, levers markup, settings form."""
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

from slopfi import db, importer, projection
from slopfi.projection import Inputs, Item, run
from slopfi.web.app import app
from factories import EVERYDAY_RULES, SALARY, add_rules
from test_trends import _stmt, _t

SRC = Path(__file__).resolve().parents[1] / "src" / "slopfi"


class FormNesting(HTMLParser):
    """Counts <form> tags opened while another form is still open: the parser drops those, so the UI breaks."""

    def __init__(self):
        super().__init__()
        self.depth = self.nested = self.forms = 0

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.forms += 1
            if self.depth:
                self.nested += 1
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "form":
            self.depth -= 1


def nested_forms(html: str) -> int:
    p = FormNesting()
    p.feed(html)
    return p.nested


def _seed(db_path, months=("07", "08", "09")):
    conn = db.connect(db_path)
    add_rules(conn, EVERYDAY_RULES)
    txns = []
    for m in months:
        txns += [_t(f"2026-{m}-05", "TESCO STORES", -300.0), _t(f"2026-{m}-10", "DELIVEROO", -160.0),
                 _t(f"2026-{m}-28", SALARY, 2000.0, "BACS")]
    importer.import_statement(conn, _stmt("2026-07-01", "2026-09-30", txns), "a.pdf", "h1")
    groceries = db.category_id_by_path(conn, "Groceries")
    sid = projection.ensure_default_scenario(conn)
    conn.close()
    return sid, groceries


# ------------------------------------------------------------------ engine
def _inputs():
    return Inputs(items=[
        Item("h:1", "House", "property", 300000.0, 3.0),
        Item("h:2", "Mortgage", "mortgage", 120000.0, 3.75, is_liability=True, payment=800.0, overpayment=300.0),
        Item("a:1", "Current", "current", 5000.0, 0.0),
        Item("a:2", "Pot", "savings", 15000.0, 3.0, contribution=500.0),
        Item("h:3", "ISA", "stocks_isa", 24000.0, 5.0, contribution=500.0),
        Item("h:4", "Stocks", "stocks", 10000.0, 5.0),
        Item("h:5", "Card", "credit_card", 700.0, 0.0),
    ], start=date(2026, 10, 1), horizon_years=10, inflation_pct=3.1, property_growth_pct=3.0)


def test_bands_are_ordered_by_liquidity_and_sum_to_net_worth():
    result = run(_inputs())
    assert result["classes"] == ["Property equity", "ISAs", "Stocks", "Cash and pots"]   # bottom to top
    bands = projection.band_series(result)
    assert set(bands) == set(result["classes"]) and all(len(v) == 121 for v in bands.values())
    for i, row in enumerate(result["rows"]):
        assert abs(sum(bands[c][i] for c in result["classes"]) - row["net_worth"]) < 0.05, i
    assert bands["Property equity"][0] == 300000.0 - 120000.0
    assert bands["Cash and pots"][0] == 5000.0 + 15000.0 + 700.0
    assert bands["Property equity"][-1] > bands["Property equity"][0]                     # mortgage paid down, house grows
    yearly = projection.yearly_rows(result)
    assert [r["year"] for r in yearly] == list(range(11)) and yearly[0]["month"] == 0


def test_compact_money_format():
    assert projection.compact_money(326429) == "£326.4k"
    assert projection.compact_money(52100) == "£52,100"
    assert projection.compact_money(99999.4) == "£99,999"
    assert projection.compact_money(100000) == "£100k"
    assert projection.compact_money(1200000) == "£1.2M"
    assert projection.compact_money(-93000) == "−£93,000"
    assert projection.compact_money(None) == "—"


def test_plan_summary_flags_saving_more_than_average_net():
    cf = {"months": ["2026-07"], "net": 1291.0}
    lev = {"explicit_contributions": 1500.0, "extra_monthly": 372.0, "derived_surplus": -209.0}
    p = projection.plan_summary(lev, cf)
    assert p["planned"] == 1872.0 and p["exceeds"] is True and abs(p["gap"] - 581.0) < 1e-9 and p["surplus"] == -209.0
    assert projection.plan_summary({"explicit_contributions": 100.0, "extra_monthly": 0.0, "derived_surplus": 1191.0}, cf)["exceeds"] is False
    empty = projection.plan_summary(lev, {"months": [], "net": 0.0})
    assert empty["exceeds"] is False and empty["average_net"] is None


def test_swatches_follow_the_ramp():
    assert projection.swatches(["Property equity", "ISAs", "Stocks", "Cash and pots", "Other"]) == {
        "Property equity": "ramp-1", "ISAs": "ramp-2", "Stocks": "ramp-3", "Cash and pots": "ramp-4", "Other": "chart-other"}


# -------------------------------------------------------------------- page
def test_no_form_is_nested_in_another_form(db_path):
    sid, _ = _seed(db_path)
    client = TestClient(app)
    r = client.post(f"/projection/{sid}/clone", data={"name": "Stretch"}, follow_redirects=False)
    other = r.headers["location"].split("=")[1]
    for path in ("/projection", f"/projection?scenario={other}", f"/projection?scenario={sid}&compare={other}", "/settings"):
        page = client.get(path).text
        assert nested_forms(page) == 0, path
    page = client.get(f"/projection?scenario={other}").text
    assert f'action="/projection/{other}/clone"' in page and f'action="/projection/{other}/delete"' in page
    assert 'data-confirm="Delete Stretch?"' in page and "assumptions go with it" in page
    assert 'onchange="this.form.submit()"' not in page and ">Apply</button>" in page


def test_scenario_actions(db_path):
    sid, _ = _seed(db_path)
    client = TestClient(app)
    page = client.get("/projection").text
    assert f'action="/projection/{sid}/delete"' not in page and "The Base scenario can't be deleted." in page
    r = client.post(f"/projection/{sid}/delete", follow_redirects=False)
    assert r.status_code == 303 and "can%27t%20be%20deleted" in r.headers["set-cookie"]
    r = client.post(f"/projection/{sid}/clone", data={"name": "   "}, follow_redirects=False)
    assert r.status_code == 303 and "Give%20the%20new%20scenario%20a%20name" in r.headers["set-cookie"]
    r = client.post(f"/projection/{sid}/clone", data={"name": "Stretch"}, follow_redirects=False)
    new = r.headers["location"].split("=")[1]
    assert "Scenario%20Stretch%20created" in r.headers["set-cookie"]
    r = client.post(f"/projection/{new}/delete", follow_redirects=False)
    assert r.headers["location"] == "/projection" and "Scenario%20deleted" in r.headers["set-cookie"]
    assert client.get(f"/projection?scenario={new}").status_code == 200       # unknown scenario falls back to Base


def test_projection_markup(db_path):
    sid, groceries = _seed(db_path)
    client = TestClient(app)
    page = client.get("/projection").text
    head = page.split("</head>")[0]
    assert "projection.css" in head
    # facts row, tiles and their definitions
    for label in ("Starting net worth", "Horizon", "Contributions", "From levers", "Average net", "Inflation"):
        assert f"<dt>{label}</dt>" in page, label
    assert page.count("Matches saved scenario") == 4 and page.count("in today's money") >= 4
    assert 'aria-live' not in page.split('id="tiles"')[1].split("</section>")[0]
    assert page.count('role="status"') == 2 and 'id="lever-status"' in page           # the toast region and the lever sentence
    assert "compounding monthly" not in page and "Income and surplus" not in page
    # levers
    assert f'aria-label="Cut Groceries"' in page and 'step="5"' in page
    assert re.search(r'id="lever-%d"[^>]*aria-valuetext="0%%, saves £0 a month"' % groceries, page)
    assert 'id="save-bar" hidden' in page and ">Save to scenario</button>" in page and 'id="levers-reset"' in page
    assert 'id="surplus-mode" form="levers-form"' in page and 'id="surplus-target" form="levers-form"' in page
    # chart twin, legend and labels
    assert "No balances yet" in page and 'id="chart-projection"' not in page       # no balances: the chart panel says why
    # the equation
    for label in ("Income", "Spending", "Net", "Already saved", "Surplus"):
        assert f"<dt>{label}</dt>" in page
    assert "Keep in current account (cautious)" in page and "Save it each month" in page


def test_sanity_notice_and_lever_values(db_path):
    sid, groceries = _seed(db_path)
    client = TestClient(app)
    assert 'id="plan-notice" hidden' in client.get("/projection").text
    r = client.post(f"/projection/{sid}/levers", data={f"save__{groceries}": "20", "surplus_mode": "save", "surplus_override": "5000",
                                                     "surplus_target": ""}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/projection").text
    assert 'id="plan-notice" hidden' not in page and "Planned saving is" in page
    assert re.search(r'id="lever-%d"[^>]*value="20"[^>]*data-saved="20"[^>]*aria-valuetext="20%%, saves £60 a month"' % groceries, page)
    assert "£5,060 a month" in page                                                 # From levers: £60 cuts + £5,000 surplus
    api = client.get(f"/api/projection?scenario={sid}&savings={groceries}:20&surplus_mode=save&surplus_override=5000").json()
    assert api["plan"]["exceeds"] is True and api["milestones"][0]["month"] == 12 and api["payoff"] is None
    for i in range(len(api["net_worth"])):
        assert abs(sum(api["series"][c][i] for c in api["classes"]) - api["net_worth"][i]) < 0.05


def test_payoff_label_and_compare_columns(db_path):
    sid, _ = _seed(db_path)
    conn = db.connect(db_path)
    conn.execute("INSERT INTO holdings (name, kind, value, valued_at, created_at) VALUES ('House', 'property', 300000, '2026-10-01', '2026-10-01')")
    conn.execute("""INSERT INTO holdings (name, kind, value, valued_at, rate, monthly_payment, parent_id, created_at)
                    VALUES ('Mortgage: House', 'mortgage', 20000, '2026-10-01', 2.0, 800, 1, '2026-10-01')""")
    conn.commit()
    conn.close()
    client = TestClient(app)
    page = client.get("/projection").text
    assert 'id="twin-projection" hidden' in page and "<td>Today</td>" in page
    assert 'data-sw="ramp-1">Property equity' in page and 'data-sw="ink">In today\'s money' in page
    assert 'id="ghost-key" hidden>Saved scenario' in page and 'id="chart-labels"' in page and 'id="chart-tip"' in page
    assert re.search(r'aria-label="Projected net worth £[\d.,]+k? in 10 years', page)
    m = re.search(r"Mortgage paid off · ([A-Z][a-z]{2} 20\d\d)", page)
    assert m and re.search(r"Mortgage paid off in month \d+, " + m.group(1), page)
    other = client.post(f"/projection/{sid}/clone", data={"name": "Stretch"}, follow_redirects=False).headers["location"].split("=")[1]
    page = client.get(f"/projection?scenario={sid}&compare={other}").text
    assert 'data-sw="ink-2">Stretch' in page and ">Difference</th>" in page and "compared with <b>Stretch</b>" in page


def test_assumptions_errors_open_the_disclosure(db_path):
    sid, _ = _seed(db_path)
    client = TestClient(app)
    r = client.post(f"/projection/{sid}/assumptions", data={"inflation_pct": "three", "horizon_years": "7"})
    assert r.status_code == 400 and 'id="assumptions" open' in r.text and 'id="err-inflation_pct"' in r.text
    assert 'value="three"' in r.text and 'value="7"' in r.text                        # entries survive the error
    r = client.post(f"/projection/{sid}/assumptions", data={"horizon_years": "80"})
    assert r.status_code == 400 and "between 1 and 50" in r.text
    assert re.search(r'id="assumptions"\s*>', client.get("/projection").text)           # closed when nothing is wrong


def test_settings_page_is_a_form_grid_with_help_and_errors(db_path):
    client = TestClient(app)
    page = client.get("/settings").text
    assert 'class="form-grid settings-grid"' in page and "<h2 id=\"defaults-h\">Projection defaults</h2>" in page
    assert page.count('inputmode="decimal"') == len(projection.SETTING_DEFAULTS)
    assert "Built-in default 3.1%." in page and "Built-in default 10." in page and ">Save settings</button>" in page
    defaults = page.split('id="defaults-h"', 1)[1]           # the Household panel above has the people table
    assert 'aria-describedby="help-proj-inflation"' in page and "<table" not in defaults
    r = client.post("/settings", data={"proj.inflation": "abc", "proj.rate.stocks": "6"})
    assert r.status_code == 400 and 'id="err-proj.inflation"' in r.text and 'value="abc"' in r.text
    assert 'aria-describedby="help-proj-inflation err-proj.inflation"' in r.text
    assert nested_forms(r.text) == 0


def test_page_css_uses_tokens_only():
    css = (SRC / "web" / "static" / "projection.css").read_text()
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", css)
    html = (SRC / "web" / "templates" / "projection.html").read_text() + (SRC / "web" / "templates" / "settings.html").read_text()
    assert "style=" not in html and "size=" not in html and 'onchange=' not in html
