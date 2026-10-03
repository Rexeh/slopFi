"""The definition of done for the UI, as tests: a source audit of the stylesheets, templates and
scripts, and a rendered audit of every page against a seeded database and an empty one."""
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from slopfi import db, importer, projection
from slopfi.parsers import ParsedStatement, ParsedTransaction
from slopfi.web.app import app

from factories import CARD_ID, ENERGY, EVERYDAY_RULES, JOINT_HOLDER, JOINT_ID, SALARY, add_rules

WEB = Path(__file__).resolve().parents[1] / "src" / "slopfi" / "web"
STATIC, TEMPLATES = WEB / "static", WEB / "templates"
TEMPLATE_TEXT = {p.name: p.read_text() for p in sorted(TEMPLATES.glob("*.html"))}
SCRIPT_TEXT = {p.name: p.read_text() for p in sorted(STATIC.glob("*.js"))}
PAGES = ["/", "/review", "/transactions", "/spending", "/networth", "/projection", "/statements", "/categories",
         "/settings"]


# =================================================================== source audit
def _strip_tokens(css: str) -> str:
    """Drop comments and the :root token and theme blocks (light, dark media query, dark and light overrides)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.sub(r":root[^{]*\{[^{}]*\}", "", css)


@pytest.mark.parametrize("path", sorted(STATIC.glob("*.css")), ids=lambda p: p.name)
def test_no_hex_colour_outside_the_token_blocks(path):
    rest = _strip_tokens(path.read_text())
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", rest), path.name
    assert not re.findall(r"\brgba?\(\s*\d", rest), path.name


def test_style_css_token_blocks_are_the_only_hex():
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "style.css").read_text(), flags=re.S)
    hexes = len(re.findall(r"#[0-9a-fA-F]{6}\b", css))
    assert hexes > 60 and hexes == len(re.findall(r"#[0-9a-fA-F]{6}\b", "".join(re.findall(r":root[^{]*\{[^{}]*\}", css))))


@pytest.mark.parametrize("pattern,why", [
    (r"""onchange=["']this\.form\.submit""", "auto-submitting select"),
    (r"\bonchange=|\bonclick=|\boninput=", "inline handler"),
    (r"\sstyle=", "inline style="),
    (r"<input[^>]*\ssize=", "size= on an input"),
    (r"\?msg=", "?msg= flash"),
    (r"(?<![.\w])confirm\(", "bare confirm("),
    (r"text-transform|uppercase", "uppercase label"),
    (r"[▲▼△▽]|&#9650;|&#9660;", "text arrow instead of the arrow icons"),
])
def test_templates_are_clean(pattern, why):
    hits = [name for name, text in TEMPLATE_TEXT.items() if re.search(pattern, text, re.I)]
    assert not hits, f"{why} in {hits}"


def test_scripts_have_no_bare_confirm_msg_or_text_arrows():
    for name, text in SCRIPT_TEXT.items():
        if name.endswith(".min.js"):
            continue
        assert not re.search(r"(?<![.\w])confirm\(\s*['\"`]", text), name      # only FF.confirm(title, detail)
        assert "?msg=" not in text and "this.form.submit" not in text, name
        assert not re.search(r"[▲▼]", text), name


def test_no_row_opacity_uppercase_or_large_pence_in_css():
    for path in STATIC.glob("*.css"):
        css = _strip_tokens(path.read_text())
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            subjects = [re.split(r"[\s>+~]+", one.strip())[-1] for one in selector.split(",")]
            if any(re.match(r"(tr|td)\b", s) for s in subjects):
                assert not re.search(r"(?<![\w-])opacity\s*:", body), f"{path.name}: {selector.strip()}"
        assert not re.search(r"text-transform\s*:\s*uppercase", css), path.name


def test_every_filter_form_has_apply_and_htmx_inputs_carry_delay():
    for name, text in TEMPLATE_TEXT.items():
        for form in re.findall(r'<form[^>]*method="get".*?</form>', text, re.S):
            if "<select" in form:
                assert re.search(r">\s*Apply\s*<", form), f"{name}: GET form with selects but no Apply"
        for tag in re.findall(r"<(?:input|select)[^>]*hx-(?:get|post)[^>]*>", text):
            assert "delay:" in tag, f"{name}: htmx field without a delay: {tag[:80]}"


# ================================================================ rendered audit
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Page(HTMLParser):
    """One pass over a rendered page: headings, nav state, forms, canvases and their twins, visible text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, dict]] = []
        self.h1 = 0
        self.current_nav = []
        self.form_depth = 0
        self.nested_forms = 0
        self.canvases: list[tuple[dict, list[str]]] = []
        self.ids: dict[str, str] = {}
        self.toggles: list[dict] = []
        self.destructive: list[tuple[str, bool]] = []
        self.texts: list[str] = []
        self._skip = 0
        self._last_id = None
        self.tables_in: set[str] = set()

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "h1":
            self.h1 += 1
        if tag == "a" and a.get("aria-current") == "page" and any(t == "nav" for t, _ in self.stack):
            self.current_nav.append(a.get("href"))
        if tag == "form":
            if self.form_depth:
                self.nested_forms += 1
            self.form_depth += 1
        if "id" in a:
            self.ids[a["id"]] = tag
        if tag == "canvas":
            self.canvases.append((a, [x.get("id", "") for _, x in self.stack]))
        if "data-table-toggle" in a:
            self.toggles.append(a)
        if tag == "table":
            for _, x in self.stack:
                if x.get("id"):
                    self.tables_in.add(x["id"])
        target = a.get("action", "") + " " + a.get("hx-post", "")
        if re.search(r"/delete\b", target):
            confirmed = "data-confirm" in a or any("data-confirm" in x for _, x in self.stack)
            self.destructive.append((target.strip(), confirmed))
        if tag in ("script", "style", "template", "svg"):
            self._skip += 1
        if tag not in VOID:
            self.stack.append((tag, a))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag == "form":
            self.form_depth -= 1
        if tag in ("script", "style", "template", "svg"):
            self._skip -= 1
        for i in range(len(self.stack) - 1, -1, -1):        # tolerate optional end tags (li, p, option)
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.texts.append(data.strip())


def parse(html: str) -> Page:
    p = Page()
    p.feed(html)
    p.close()
    return p


RAW_LABELS = {"current", "credit_card", "savings", "joint", "alex", "sam", "unknown", "expense", "income",
              "transfer", "property", "mortgage", "stocks_isa", "cash_isa", "pension", "loan", "stocks", "cash",
              "contains", "prefix", "regex", "manual", "seed", "source"}


def _statement(kind, ident, name, txns, start="2026-07-01", end="2026-09-30", institution="HSBC UK",
               parser="hsbc_current"):
    return ParsedStatement(parser=parser, institution=institution, account_name=name, account_identifier=ident,
                           account_kind=kind, period_start=date.fromisoformat(start), period_end=date.fromisoformat(end),
                           opening_balance=1000.0, closing_balance=1500.0, payments_in=None, payments_out=None,
                           transactions=txns)


def _t(d, desc, amount, code=")))", **kw):
    return ParsedTransaction(date.fromisoformat(d), code, [desc], amount, **kw)


@pytest.fixture
def seeded(db_path):
    """Jul–Sep 2026 across a joint current account, a credit card and a savings pot; a house with its mortgage, an
    ISA, a spending target, a second scenario and a closed month, so every page draws every chart and table."""
    conn = db.connect(db_path)
    add_rules(conn, EVERYDAY_RULES)
    cur, card, pot = [], [], []
    for m in ("07", "08", "09"):
        cur += [_t(f"2026-{m}-05", "TESCO STORES", -320.0), _t(f"2026-{m}-10", "DELIVEROO", -95.0),
                _t(f"2026-{m}-01", ENERGY, -140.0, "DD"), _t(f"2026-{m}-12", "MYSTERY TRADER LTD", -60.0, "VIS"),
                _t(f"2026-{m}-28", SALARY, 4200.0, "BACS"),
                _t(f"2026-{m}-02", "EXAMPLE HOME LOANS / MTG 12349876", -800.0, "DD")]
        card += [_t(f"2026-{m}-14", "AMAZON.CO.UK / MARKETPLACE", -75.0), _t(f"2026-{m}-20", "SHELL EXAMPLETOWN", -60.0)]
        pot += [_t(f"2026-{m}-27", "Transfer from Personal Account", 300.0, "POT")]
    importer.import_statement(conn, _statement("current", JOINT_ID, JOINT_HOLDER, cur), "cur.pdf", "h-cur")
    importer.import_statement(conn, _statement("credit_card", CARD_ID, "Amex Alex", card, institution="American Express",
                                               parser="amex_card"), "card.pdf", "h-card")
    importer.import_statement(conn, _statement("savings", "99-30-40 12343040 pot:Savings", "Monzo pot: Savings", pot, institution="Monzo",
                                               parser="monzo_pdf"), "pot.pdf", "h-pot")
    conn.execute("UPDATE accounts SET owner = 'alex' WHERE name = 'Amex Alex'")
    conn.close()
    client = TestClient(app)
    for when, value in (("2026-08-01", "295000"), ("2026-09-15", "300000")):
        pid = db.connect(db_path).execute("SELECT id FROM holdings WHERE kind = 'property'").fetchone()
        data = {"name": "Our house", "kind": "property", "owner": "joint", "value": value, "valued_at": when,
                "mortgage_balance": "150000", "rate": "3.75", "monthly_payment": "800"}
        if pid:
            data["holding_id"] = str(pid[0])
        assert client.post("/holdings", data=data, follow_redirects=False).status_code == 303
    assert client.post("/holdings", data={"name": "Example Invest ISA", "kind": "stocks_isa", "owner": "alex", "value": "24000",
                                          "valued_at": "2026-09-15"}, follow_redirects=False).status_code == 303
    conn = db.connect(db_path)
    groceries = db.category_id_by_path(conn, "Groceries")
    sid = projection.ensure_default_scenario(conn)
    conn.close()
    assert client.post(f"/budgets/{groceries}", data={"target": "250"}, follow_redirects=False).status_code in (200, 303)
    assert client.post(f"/projection/{sid}/clone", data={"name": "Overpay"}, follow_redirects=False).status_code == 303
    assert client.post("/review/close", data={"month": "2026-07"}, follow_redirects=False).status_code == 303
    return client


@pytest.fixture
def empty(db_path):
    db.connect(db_path).close()
    return TestClient(app)


def _get_routes():
    from slopfi.web.routes import networth, overview, projection as proj, review, setup, spending, transactions
    routes = list(app.routes)
    for mod in (overview, transactions, spending, networth, proj, setup, review):
        routes += mod.router.routes
    for route in routes:
        methods = getattr(route, "methods", None) or set()
        if "GET" in methods and "{" not in route.path and not route.path.startswith(("/docs", "/redoc", "/openapi")):
            yield route.path


def test_every_get_route_returns_200_against_a_seeded_database(seeded):
    paths = sorted(set(_get_routes()))
    assert set(PAGES) <= set(paths)
    for path in paths:
        r = seeded.get(path)
        assert r.status_code == 200, (path, r.status_code)
    for path in PAGES:                                     # every month the filters offer also renders
        for month in ("2026-07", "2026-09", "all"):
            assert seeded.get(f"{path}?month={month}").status_code == 200, (path, month)


@pytest.fixture(params=["seeded", "empty"])
def pages(request):
    client = request.getfixturevalue(request.param)
    return {path: client.get(path) for path in PAGES}


@pytest.mark.parametrize("path", PAGES)
def test_page_shell_heading_and_forms(pages, path):
    r = pages[path]
    assert r.status_code == 200
    page = parse(r.text)
    assert page.h1 == 1, f"{path}: {page.h1} h1 elements"
    assert page.current_nav == [path], f"{path}: aria-current on {page.current_nav}"
    assert page.nested_forms == 0, f"{path}: a form inside a form"
    assert 'class="skip"' in r.text and 'id="toast"' in r.text and 'aria-label="Main"' in r.text
    for bad in (' style="', "?msg=", 'onchange="', " size="):
        assert bad not in r.text, f"{path}: {bad}"


@pytest.mark.parametrize("path", PAGES)
def test_every_chart_has_a_label_and_a_table_twin(pages, path):
    page = parse(pages[path].text)
    toggles = {t["data-table-toggle"]: t for t in page.toggles}
    for canvas, ancestors in page.canvases:
        assert canvas.get("role") == "img" and len(canvas.get("aria-label", "")) > 20, (path, canvas)
        wraps = [i for i in ancestors if re.fullmatch(r"chart-[\w-]+-wrap", i)]
        assert wraps, f"{path}: canvas {canvas.get('id')} outside a chart-<key>-wrap"
        key = wraps[-1][len("chart-"):-len("-wrap")]
        assert key in toggles, f"{path}: no Table toggle for {key}"
        assert toggles[key].get("aria-controls") == f"twin-{key}", (path, toggles[key])
        assert f"twin-{key}" in page.ids and f"twin-{key}" in page.tables_in, f"{path}: twin-{key} has no table"


@pytest.mark.parametrize("path", PAGES)
def test_every_destructive_form_confirms(pages, path):
    page = parse(pages[path].text)
    unconfirmed = [target for target, ok in page.destructive if not ok]
    assert not unconfirmed, f"{path}: {unconfirmed}"


@pytest.mark.parametrize("path", PAGES)
def test_no_raw_enum_values_in_rendered_text(pages, path):
    page = parse(pages[path].text)
    raw = [t for t in page.texts if t in RAW_LABELS or re.search(r"\b[a-z]+_[a-z_]+\b", t)]
    assert not raw, f"{path}: {raw[:5]}"


def test_seeded_pages_show_what_was_seeded(seeded, db_path):
    """Guards the fixture: the audit above really saw charts, deletes and enum-bearing rows."""
    seen = {p: parse(seeded.get(p).text) for p in ("/", "/spending", "/networth", "/projection", "/statements")}
    other = db.connect(db_path).execute("SELECT id FROM scenarios WHERE name = 'Overpay'").fetchone()[0]
    overpay = parse(seeded.get(f"/projection?scenario={other}").text)
    assert overpay.destructive and all(ok for _, ok in overpay.destructive) and overpay.nested_forms == 0
    assert len(seen["/"].canvases) == 2 and len(seen["/spending"].canvases) >= 6
    assert len(seen["/networth"].canvases) == 2 and len(seen["/projection"].canvases) == 1
    assert len(seen["/statements"].destructive) >= 3 and len(seen["/networth"].destructive) >= 1
    statements = " ".join(seen["/statements"].texts)
    assert "Credit card" in statements and "Savings" in statements and "Joint" in statements and "Alex" in statements


def test_transactions_hide_low_priority_columns_at_768():
    page = TEMPLATE_TEXT["_txn_results.html"]
    heads = re.findall(r'\bth\("(\w+)", "[^"]*"([^)]*)\)', page)          # the sortable headers, after Select
    low = [i + 2 for i, (_, rest) in enumerate(heads) if 'priority="low"' in rest]
    assert low == [3, 7]
    css = (STATIC / "transactions.css").read_text()
    assert re.search(r"@media \(max-width: 768px\).*?nth-child\(3\).*?nth-child\(7\)[^{]*\{\s*display: none", css, re.S)
