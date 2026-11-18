"""Browser tests: the app runs in a thread inside pytest against a fresh copy of the demo household per test,
and Playwright drives Chromium through it. Run them alone with `uv run pytest -m ui`, skip them with -m "not ui".

The server is in-process on purpose: the app reads SLOPFI_DB on every request, so each test points it at its own
copy, and the suite's monkeypatches (no built-in rules) apply to the server too."""
from __future__ import annotations

import shutil
import socket
import threading
import time
from datetime import date

import pytest
import uvicorn

from slopfi import clock
from slopfi.demo import build as demo_build

REFERENCE = date(2026, 10, 3)   # the demo's latest month is October 2026, still open
TODAY = date(2026, 11, 18)      # the app's today: six weeks on, so a Monzo balance is stale and some renewals are due


def pytest_collection_modifyitems(items):
    for item in items:
        if "/tests/ui/" in str(item.fspath):
            item.add_marker(pytest.mark.ui)


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory):
    return demo_build.build(tmp_path_factory.mktemp("ui-demo"), seed=42, reference=REFERENCE)


@pytest.fixture(scope="session")
def base_url():
    """Overrides pytest-playwright's base_url, so page.goto("/categories") works."""
    from slopfi.web.app import app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


@pytest.fixture(autouse=True)
def _pinned_today(monkeypatch):
    """A fixed today, so what is due, stale or open never drifts with the calendar."""
    monkeypatch.setattr(clock, "today", lambda: TODAY)


@pytest.fixture(autouse=True)
def household(demo_db, tmp_path, monkeypatch):
    """A fresh copy of the demo database for each test: tests change data freely."""
    path = tmp_path / "slopfi.db"
    shutil.copy(demo_db.db_path, path)
    monkeypatch.setenv("SLOPFI_DB", str(path))
    monkeypatch.setenv("SLOPFI_SOURCES", str(demo_db.out / "sources.toml"))
    return path


@pytest.fixture(autouse=True)
def _page_guard(page):
    """No network beyond the app (Google Fonts would stall the load event offline). A JavaScript error, a console
    error or a 5xx response fails the test; 4xx are the app's validation answers and are asserted where expected."""
    errors: list[str] = []
    page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
    page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error"
            and not m.text.startswith("Failed to load resource") else None)
    page.on("response", lambda r: errors.append(f"{r.status} from {r.url}") if r.status >= 500 else None)
    page.set_default_timeout(5000)
    yield page
    assert not errors, "\n".join(errors)


