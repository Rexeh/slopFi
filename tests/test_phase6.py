"""Phase 6: polish. Token contrast in both schemes (scripts/check_contrast.py, parsed from style.css), motion and
reduced motion, the Transactions default month and the favicon. The §8 audit lives in test_done.py."""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "src" / "slopfi" / "web"
STATIC, TEMPLATES = WEB / "static", WEB / "templates"
CSS = (STATIC / "style.css").read_text()


# ------------------------------------------------------------------ tokens
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_contrast.py"
_spec = importlib.util.spec_from_file_location("check_contrast", SCRIPT)
check_contrast = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_contrast)
SCHEMES = check_contrast.schemes()
LIGHT, DARK = SCHEMES["light"], SCHEMES["dark"]


def test_contrast_script_passes_in_both_schemes():
    """scripts/check_contrast.py parses the tokens from style.css and exits 0 only if every pair passes."""
    run = subprocess.run([sys.executable, str(SCRIPT), "--failing"], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "light: 82/82 pass" in run.stdout and "dark: 82/82 pass" in run.stdout


@pytest.mark.parametrize("row", check_contrast.check(),
                         ids=lambda r: f"{r['scheme']}-{r['kind']}-{r['fg']}-on-{r['bg']}")
def test_contrast_pair(row):
    assert row["ok"], f"{row['scheme']}: {row['fg']} on {row['bg']} is {row['ratio']:.2f}:1, needs {row['min']}"


def test_contrast_script_catches_a_failing_token():
    css = (STATIC / "style.css").read_text().replace("--color-ink-3: #6a655b", "--color-ink-3: #9a958b", 1)
    failing = [r for r in check_contrast.check(css) if not r["ok"]]
    assert failing and all(r["fg"] == "ink-3" and r["scheme"] == "light" for r in failing)


def test_reduced_motion_covers_every_animation():
    """Each keyframe animation and the view transition is switched off under prefers-reduced-motion."""
    reduced = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", CSS, re.S).group(1)
    assert "animation-duration: 0ms" in reduced and "transition-duration: 0ms" in reduced
    assert "::view-transition" in reduced
    assert re.search(r"@view-transition\s*\{\s*navigation:\s*auto", CSS)
    assert re.search(r"prefers-reduced-motion: reduce\) \{ @view-transition \{ navigation: none", CSS)
    for name in re.findall(r"@keyframes ([\w-]+)", CSS):
        assert re.search(rf"animation:[^;]*\b{name}\b", CSS), f"unused keyframes {name}"
    js = (STATIC / "app.js").read_text()
    assert "C.defaults.animation = reduced.matches ? false" in js        # Chart.js off too


# ------------------------------------------------- transactions default month
def test_transactions_opens_on_the_latest_open_month_and_keeps_all_time(db_path):
    from fastapi.testclient import TestClient
    from slopfi import review
    from slopfi.web.app import app
    from test_phase2 import _history

    conn = _history(db_path)                     # Mar–Jun 2026 on one account
    client = TestClient(app)
    page = client.get("/transactions").text
    assert '<option value="2026-06" selected>' in page and '<option value="all" >All time</option>' in page
    assert "in Jun 2026" in page and "across all months" not in page
    review.close_month(conn, "2026-06")          # closing June moves the default back a month
    conn.close()
    assert '<option value="2026-05" selected>' in client.get("/transactions").text
    every = client.get("/transactions?month=all").text
    assert '<option value="all" selected>' in every and "across all months" in every
    # a link carrying any filter but no month (Spending's category links, search) still spans all months
    assert "across all months" in client.get("/transactions?uncategorised=1").text


# ----------------------------------------------------------------- favicon
def test_favicon_is_the_logo_mark():
    from fastapi.testclient import TestClient
    from slopfi.web.app import app

    base = (TEMPLATES / "base.html").read_text()
    assert re.search(r'<link rel="icon" type="image/png" href="\{\{ static_url\(\'logo-mark.png\'\) \}\}">', base)
    assert (STATIC / "logo-mark.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "static_url('logo-mark.png')" in base.split('class="brand-link"')[1].split("</a>")[0]   # the same mark as the rail
    r = TestClient(app).get("/favicon.ico", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/static/logo-mark.png"
