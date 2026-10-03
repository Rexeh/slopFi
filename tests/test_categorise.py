import pytest

from slopfi import categorise, db

from factories import rule


def test_normalise_and_suggest():
    assert categorise.normalise("  tesco   superstore ") == "TESCO SUPERSTORE"
    assert categorise.suggest_pattern("INT'L 0099001234 / CAFE CENTRAL / MALAGA ES") == "CAFE CENTRAL"
    assert categorise.suggest_pattern("ESSO EXAMPLE ROAD / EXAMPLETOWN") == "ESSO EXAMPLE ROAD"
    assert categorise.suggest_pattern("amazon.co.uk / 123-4567890") == "AMAZON.CO.UK"


# Rules that exercise each part of the engine: the three match types, a type code, an amount band, and priority.
ENGINE_RULES = [
    rule("MTG", "Housing/Mortgage", priority=10, type_code="DD"),
    rule("OVERPAYMENT", "Housing/Mortgage overpayment", priority=10),
    rule("RIVERA", "Transfers/Between own accounts", priority=20),
    rule(r"\b(TESCO|SAINSBURYS)\b", "Groceries/Supermarket", "regex"),
    rule("CAFE CENTRAL", "Eating out/Restaurants & cafes"),
    rule("NON-STERLING", "Bank charges/FX fees", priority=5),
    rule("CASH", "Cash/Cash withdrawal", "prefix", type_code="ATM"),
    rule("OVERDRAFT INTEREST", "Bank charges/Interest", "prefix"),
    rule(r"^(ESSO|SHELL) ", "Transport/Fuel", "regex", amount_max=-20.0),
    rule("THE OLD BELL", "Eating out/Pubs & bars"),
    rule("TAX FREE CHILDCARE", "Children/Childcare"),
    rule("CO-OP", "Groceries/Local shop", priority=10),
    rule("EXAMPLETOWN", "Other spending", priority=90),
]


def test_rules_file_what_they_declare(conn, make_rules):
    make_rules(conn, ENGINE_RULES)
    rules = categorise.load_rules(conn)
    cases = [
        ("EXAMPLE HOME LOANS / MTG 12349876", "DD", -1145.0, "Mortgage"),
        ("EXAMPLE HOME LOANS / OVERPAYMENT", "SO", -200.0, "Mortgage overpayment"),
        ("Sam Rivera / JointBills", "CR", 1350.0, "Between own accounts"),          # contains: case-insensitive
        ("A RIVERA / MONZO SPENDING", "SO", -700.0, "Between own accounts"),
        ("TESCO STORES 2041 / EXAMPLETOWN", ")))", -96.40, "Supermarket"),         # regex beats a later catch-all
        ("INT'L 0099001234 / CAFE CENTRAL / MALAGA ES / EUR 23.80 @ 1.1720 / Visa Rate", ")))", -20.31,
         "Restaurants & cafes"),
        ("NON-STERLING / TRANSACTION FEE", "DR", -0.56, "FX fees"),
        ("CASH EXAMPLETOWN HIGH ST / @18:34", "ATM", -60.0, "Cash withdrawal"),
        ("OVERDRAFT INTEREST / TO 24JUN2026", "DR", -0.12, "Interest"),
        ("ESSO EXAMPLE ROAD / EXAMPLETOWN", ")))", -58.40, "Fuel"),
        ("The Old Bell / Exampletown", ")))", -14.0, "Pubs & bars"),
        ("Tax Free Childcare / 12345678901", "BP", -450.0, "Childcare"),
    ]
    for desc, code, amount, expected in cases:
        found = categorise.find_rule(rules, desc, code, amount)
        assert found is not None, desc
        name = conn.execute("SELECT name FROM categories WHERE id = ?", (found["category_id"],)).fetchone()["name"]
        assert name == expected, f"{desc}: got {name}"
    # priority: the specific rule (10) wins over the catch-all (90) that also matches
    assert categorise.find_rule(rules, "CO-OP GROUP 070512 / EXAMPLETOWN", ")))", -3.95)["pattern"] == "CO-OP"
    # amount band: a small fuel-station spend is not fuel, so it falls through to the catch-all
    assert categorise.find_rule(rules, "ESSO EXAMPLE ROAD / EXAMPLETOWN", ")))", -15.20)["pattern"] == "EXAMPLETOWN"
    assert categorise.find_rule(rules, "ESSO EXAMPLE ROAD", ")))", -15.20) is None
    # type code: the same words with another payment type do not match
    assert categorise.find_rule(rules, "EXAMPLE HOME LOANS / MTG 12349876", "SO", -1145.0) is None
    assert categorise.find_rule(rules, "CASH BACK OFFER", "CR", 5.0) is None
    # prefix anchors at the start; contains does not
    assert categorise.find_rule(rules, "REFUND OVERDRAFT INTEREST", "CR", 0.12) is None


def test_disabled_rules_are_skipped(conn, make_rules):
    (rule_id,) = make_rules(conn, [rule("TESCO", "Groceries/Supermarket", enabled=False)])
    assert categorise.find_rule(categorise.load_rules(conn), "TESCO STORES", ")))", -10.0) is None
    assert [r["id"] for r in categorise.load_rules(conn, enabled_only=False)] == [rule_id]


def test_manual_override_survives_rules(conn, make_rules):
    make_rules(conn, [rule("TESCO", "Groceries/Supermarket")])
    cat_groc = db.category_id_by_path(conn, "Groceries/Supermarket")
    cat_gift = db.category_id_by_path(conn, "Shopping/Gifts")
    with conn:
        conn.execute("INSERT INTO accounts (name, kind, owner) VALUES ('t', 'current', 'joint')")
        conn.execute("INSERT INTO statements (account_id, source_name, file_sha256, parser, period_start, period_end, imported_at) VALUES (1,'s','h','p','2026-01-01','2026-01-31','now')")
        conn.execute("INSERT INTO transactions (statement_id, account_id, seq, date, type_code, description, raw_lines, amount, fingerprint) VALUES (1,1,1,'2026-01-02',')))','TESCO STORES','[]',-10,'f1')")
    assert categorise.apply_rules(conn) == 1
    row = conn.execute("SELECT category_id, categorised_by FROM transactions WHERE id = 1").fetchone()
    assert row["category_id"] == cat_groc and row["categorised_by"] == "rule"
    categorise.set_category(conn, 1, cat_gift)
    assert categorise.apply_rules(conn, include_rule_categorised=True) == 0
    row = conn.execute("SELECT category_id, categorised_by FROM transactions WHERE id = 1").fetchone()
    assert row["category_id"] == cat_gift and row["categorised_by"] == "manual"


def test_create_rule_rejects_bad_regex(conn):
    import re
    with pytest.raises(re.error):
        categorise.create_rule(conn, "(", db.category_id_by_path(conn, "Groceries"), match_type="regex")
