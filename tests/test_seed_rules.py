"""The built-in rules for common UK trading names (seed/rules.json), from lists the banks publish."""
import json
from importlib import resources

import pytest

from slopfi import categorise, db

SEED = json.loads(resources.files("slopfi.seed").joinpath("rules.json").read_text())


SEED_RULES_VERSION = db.SEED_RULES_VERSION  # captured before conftest switches seeding off


@pytest.fixture
def seeded(monkeypatch):
    monkeypatch.setattr(db, "SEED_RULES_VERSION", SEED_RULES_VERSION)
    c = db.connect(":memory:")
    yield c
    c.close()


def test_every_built_in_rule_is_added_once_as_seed(seeded):
    rows = seeded.execute("SELECT source, priority FROM rules").fetchall()
    assert len(rows) == len(SEED)
    assert {r["source"] for r in rows} == {"seed"} and min(r["priority"] for r in rows) == 200
    db.init_db(seeded)
    assert seeded.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == len(SEED)


def test_a_new_seed_version_adds_only_the_missing_rules(seeded, monkeypatch):
    seeded.execute("DELETE FROM rules WHERE pattern = 'NETFLIX'")
    monkeypatch.setattr(db, "SEED_RULES_VERSION", SEED_RULES_VERSION + 1)
    db.init_db(seeded)
    assert seeded.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == len(SEED)


def test_a_deleted_built_in_rule_stays_deleted(seeded):
    seeded.execute("DELETE FROM rules WHERE pattern = 'APPLEGREEN'")
    db.init_db(seeded)
    assert seeded.execute("SELECT COUNT(*) FROM rules WHERE pattern = 'APPLEGREEN'").fetchone()[0] == 0


def _category(conn, description, amount=-10.0):
    rule = categorise.find_rule(categorise.load_rules(conn), description, None, amount)
    if rule is None:
        return None
    from slopfi.rules_io import _category_path
    return _category_path(conn, rule["category_id"])


@pytest.mark.parametrize("description, category", [
    ("AMZ*Prime UK", "Leisure/Subscriptions"),
    ("APPLEGREEN M6 SOUTHBOUND", "Transport/Fuel"),
    ("B365 INTERNET GIBRALTAR", "Leisure/Lottery & competitions"),
    ("BET365", "Leisure/Lottery & competitions"),
    ("CAPITA TV LICENCE", "Utilities/TV & streaming"),
    ("DOMESTIC & GENERAL", "Housing/Home insurance"),
    ("EE LIMITED", "Utilities/Broadband & phone"),
    ("NEWDAY LTD", "Transfers/Credit card payment"),
    ("TFL TRAVEL CH", "Transport/Public transport"),
    ("WHO*INTERNET", "Leisure/Lottery & competitions"),
    ("AMZNMktplace LONDON GBR", "Shopping/Online"),
    ("Amazon Prime*AB12C LONDON", "Leisure/Subscriptions"),
    ("UBER TRIP HTTPS://HELP.UB", "Transport/Taxi & ride hailing"),
    ("UBER * EATS PENDING", "Eating out/Takeaway & delivery"),
    ("MICROSOFT*XBOX LIVE", "Leisure/Hobbies"),
    ("MSFT * BILLING", "Leisure/Subscriptions"),
    ("O2 UK PAY & GO", "Utilities/Broadband & phone"),
    ("ACCOUNTSHIELD.CO.UK", "Leisure/Subscriptions"),
    ("ASDA STORES / BRISTOL", "Groceries/Supermarket"),
    ("BT GROUP PLC", "Utilities/Broadband & phone"),
])
def test_trading_names_are_categorised(seeded, description, category):
    assert _category(seeded, description) == category


@pytest.mark.parametrize("description", ["COFFEE HOUSE", "CHBAKERY", "GREGGS CHIPPING NORTON", "THE KINGFISHER PUB",
                                         "THE ADMIRAL PUB", "O2 ARENA LONDON", "TOSTADA BAR", "HAVENS HOSPICE SHOP"])
def test_lookalikes_are_left_alone(seeded, description):
    assert _category(seeded, description, amount=-5.0) is None


def test_child_benefit_matches_only_money_in(seeded):
    assert _category(seeded, "CHB 1234567890", amount=96.25) == "Income/Other income"
    assert _category(seeded, "CHB 1234567890", amount=-96.25) is None
