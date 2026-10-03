"""The household setting: owner choices, the sidebar subtitle, legacy owner keys, and the Settings panel."""
from __future__ import annotations

import json
import re
from urllib.parse import unquote

from fastapi.testclient import TestClient

from slopfi import db, household, importer, reports
from slopfi.web.app import app


def _flash(response) -> dict:
    raw = response.headers.get("set-cookie", "").split("flash=")[1].split(";")[0]
    return json.loads(unquote(raw.strip('"')))


def _options(page: str, select_name: str) -> list[str]:
    block = re.search(rf'<select name="{select_name}"[^>]*>(.*?)</select>', page, re.S).group(1)
    return re.findall(r">([^<]+)</option>", block)


# ----------------------------------------------------------------- model
def test_defaults(conn):
    assert household.get(conn) == {"name": "My household", "people": []}
    assert household.owner_choices(conn) == {"joint": "Joint", "unknown": "Unknown"}
    assert household.subtitle(conn) == "My household"
    household.set_name(conn, "The Example family")
    assert household.subtitle(conn) == "The Example family"


def test_owner_choices_and_subtitle_follow_the_people(conn):
    household.add_person(conn, "Alex")
    household.add_person(conn, "Sam")
    assert list(household.owner_choices(conn).items()) == [("joint", "Joint"), ("alex", "Alex"), ("sam", "Sam"),
                                                           ("unknown", "Unknown")]
    assert household.subtitle(conn) == "Household · Alex and Sam"
    household.add_person(conn, "Jo Bloggs")
    assert household.subtitle(conn) == "Household · Alex, Sam and Jo Bloggs"
    assert household.people(conn)[-1] == {"key": "jo_bloggs", "name": "Jo Bloggs"}
    assert json.loads(db.get_setting(conn, "household.people"))[0] == {"key": "alex", "name": "Alex"}


def test_add_rename_remove_rules(conn):
    household.add_person(conn, "Alex")
    for bad in ("", "  ", "alex"):
        try:
            household.add_person(conn, bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    household.add_person(conn, "Joint")                       # a reserved key gets a different one
    assert household.people(conn)[-1]["key"] == "joint_2"
    household.rename_person(conn, "alex", "Alexandra")
    assert household.owner_choices(conn)["alex"] == "Alexandra"
    importer.get_or_create_account(conn, "Card", "alex", "credit_card")
    assert household.owned_counts(conn, "alex") == {"accounts": 1, "holdings": 0}
    assert household.remove_person(conn, "alex") is False
    assert household.remove_person(conn, "joint_2") is True
    assert [p["key"] for p in household.people(conn)] == ["alex"]


def test_legacy_owner_keys_are_added_title_cased_on_first_load(conn):
    importer.get_or_create_account(conn, "Card one", "pat", "credit_card")
    importer.get_or_create_account(conn, "Current", "joint", "current")
    reports.save_holding(conn, {"name": "ISA", "kind": "stocks_isa", "owner": "jo", "value": 100.0, "valued_at": "2026-01-01"})
    assert db.get_setting(conn, "household.people") is None
    assert household.people(conn) == [{"key": "jo", "name": "Jo"}, {"key": "pat", "name": "Pat"}]
    assert json.loads(db.get_setting(conn, "household.people")) == [{"key": "jo", "name": "Jo"}, {"key": "pat", "name": "Pat"}]
    assert household.subtitle(conn) == "Household · Jo and Pat"
    household.rename_person(conn, "pat", "Patricia")           # renaming sticks: the key is already listed
    assert household.owner_choices(conn)["pat"] == "Patricia"


# ------------------------------------------------------------------ web
def test_every_owner_select_offers_joint_each_person_and_unknown(db_path):
    conn = db.connect(db_path)
    household.add_person(conn, "Alex")
    household.add_person(conn, "Sam")
    importer.get_or_create_account(conn, "Monzo Alex", "alex", "current")
    conn.close()
    client = TestClient(app)
    statements = client.get("/statements").text
    assert _options(statements, "new_account_owner") == ["Joint", "Alex", "Sam", "Unknown"]
    assert _options(statements, "owner") == ["Joint", "Alex", "Sam", "Unknown"]
    networth = client.get("/networth").text
    assert _options(networth, "owner") == ["Joint", "Alex", "Sam", "Unknown"]
    assert "<small>Household · Alex and Sam</small>" in client.get("/").text


def test_legacy_keys_show_in_the_app_without_any_setup(db_path):
    conn = db.connect(db_path)
    importer.get_or_create_account(conn, "Card", "pat", "credit_card")
    conn.close()
    client = TestClient(app)
    page = client.get("/statements").text
    assert "<small>Household · Pat</small>" in page and "<td>Pat</td>" in page
    assert _options(page, "owner") == ["Joint", "Pat", "Unknown"]


def test_account_owner_must_be_a_household_choice(db_path):
    conn = db.connect(db_path)
    acct = importer.get_or_create_account(conn, "Card", "unknown", "credit_card")
    household.add_person(conn, "Sam")
    conn.close()
    client = TestClient(app)
    r = client.post(f"/accounts/{acct}", data={"name": "Card", "owner": "nobody", "kind": "credit_card"}, follow_redirects=False)
    assert _flash(r)["kind"] == "error"
    r = client.post(f"/accounts/{acct}", data={"name": "Card", "owner": "sam", "kind": "credit_card"}, follow_redirects=False)
    assert _flash(r)["kind"] == "success"


def test_settings_household_panel(db_path):
    client = TestClient(app)
    page = client.get("/settings").text
    assert '<h2 id="household-h">Household</h2>' in page and 'value="My household"' in page and "No people yet" in page

    r = client.post("/settings/household", data={"name": "The Example family"}, follow_redirects=False)
    assert r.status_code == 303 and _flash(r)["message"] == "Household name saved"
    assert client.post("/settings/household", data={"name": " "}).status_code == 400

    r = client.post("/settings/household/people", data={"name": "Alex"}, follow_redirects=False)
    assert _flash(r)["message"] == "Alex added"
    client.post("/settings/household/people", data={"name": "Sam"})
    r = client.post("/settings/household/people", data={"name": "sam"})
    assert r.status_code == 400 and "already in the household" in r.text
    r = client.post("/settings/household/people/sam/rename", data={"name": "Samira"}, follow_redirects=False)
    assert _flash(r)["message"] == "Renamed to Samira"

    conn = db.connect(db_path)
    importer.get_or_create_account(conn, "Monzo Alex", "alex", "current")
    importer.get_or_create_account(conn, "Amex Alex", "alex", "credit_card")
    conn.close()
    page = client.get("/settings").text
    assert "<small>Household · Alex and Samira</small>" in page
    assert "Owns 2 accounts" in page and 'aria-label="Rename Alex"' in page
    assert 'action="/settings/household/people/samira/delete"' not in page            # keys never change on rename
    assert 'action="/settings/household/people/sam/delete"' in page and 'data-confirm="Remove Samira?"' in page

    r = client.post("/settings/household/people/alex/delete", follow_redirects=False)
    assert _flash(r)["kind"] == "error" and _flash(r)["message"].startswith("Alex owns 2 accounts")
    r = client.post("/settings/household/people/sam/delete", follow_redirects=False)
    assert _flash(r)["message"] == "Samira removed"
    assert _options(client.get("/networth").text, "owner") == ["Joint", "Alex", "Unknown"]
