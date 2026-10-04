"""The Rivera household: a deterministic, entirely fictional family and twelve months of its transactions.

`generate(seed, reference)` returns the same household for the same seed and reference date. The year is the
twelve complete calendar months before the reference date's month. Amounts are kept in integer pence so every
running balance ties up exactly; the writers turn them into statement files.

Accounts:
  * joint current account (HSBC-style PDF): both salaries' landing point, bills, mortgage, the weekly shop;
  * Alex's current account with two pots, Savings and Rainy day (Monzo-style PDF, one file for the year);
  * Sam's current account (Monzo-style CSV export, no balances);
  * Alex's credit card (Amex-style PDF), paid in full each month by direct debit from the joint account.
"""
from __future__ import annotations

import calendar
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

HOUSEHOLD_NAME = "The Rivera household"
PEOPLE = [{"key": "alex", "name": "Alex"}, {"key": "sam", "name": "Sam"}]
FULL_NAMES = {"alex": "Alex Rivera", "sam": "Sam Rivera"}
ADDRESS = ["1 Example Street", "Exampletown", "EX1 1AA"]
EMPLOYER = "ACME ANALYTICS LTD"
SAM_EMPLOYER = "EXAMPLETOWN SCHOOLS TRUST"
CAR_REPAIR_TAG = "car_repair"


@dataclass
class Txn:
    day: date
    pence: int                      # signed: negative = money leaving the account
    lines: list[str]                # description as the bank prints it, one entry per printed line
    code: str = ""                  # the format's own type code (HSBC "DD", Monzo CSV "Card payment", ...)
    fx: tuple[str, int, str] | None = None   # (currency, foreign amount in minor units, rate as printed)
    fee_pence: int = 0              # Amex: the non-sterling fee included in the sterling amount
    category: str = ""              # Monzo CSV: the bank's own category label
    tag: str = ""                   # a transaction the builder looks for later (the car repair)

    @property
    def amount(self) -> float:
        return self.pence / 100


@dataclass
class Pot:
    name: str
    pot_type: str
    opening: int
    rate: float                     # annual %, paid monthly
    txns: list[Txn] = field(default_factory=list)
    balance: int = 0


@dataclass
class Account:
    key: str
    name: str                       # what the app calls it (sources.toml)
    owner: str
    kind: str                       # current | credit_card
    fmt: str                        # hsbc_pdf | amex_pdf | barclays_pdf | monzo_pdf | monzo_csv
    folder: str                     # statements/<person>/<account>
    holder: str                     # the name printed on the statement
    sort_code: str = ""
    number: str = ""
    opening: int = 0                # balance before the first month (credit card: negative = owed)
    txns: list[Txn] = field(default_factory=list)
    pots: list[Pot] = field(default_factory=list)

    def balance_on(self, day: date) -> int:
        return self.opening + sum(t.pence for t in self.txns if t.day <= day)


@dataclass
class Household:
    seed: int
    reference: date
    months: list[tuple[int, int]]
    accounts: dict[str, Account]
    name: str = HOUSEHOLD_NAME
    people: list[dict] = field(default_factory=lambda: [dict(p) for p in PEOPLE])

    @property
    def start(self) -> date:
        y, m = self.months[0]
        return date(y, m, 1)

    @property
    def end(self) -> date:
        y, m = self.months[-1]
        return date(y, m, calendar.monthrange(y, m)[1])


def month_window(reference: date, n: int = 12) -> list[tuple[int, int]]:
    """The n complete calendar months before the reference date's month, oldest first."""
    y, m = reference.year, reference.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append((y, m))
    return list(reversed(out))


def _p(pounds: float) -> int:
    return int(round(pounds * 100))


def _money(pence: int) -> str:
    return f"{abs(pence) / 100:,.2f}"


def _last_working_day(y: int, m: int) -> date:
    d = date(y, m, calendar.monthrange(y, m)[1])
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


class _Gen:
    def __init__(self, seed: int, reference: date):
        self.rng = random.Random(seed)
        self.reference = reference
        self.months = month_window(reference)
        self.joint = Account("joint-hsbc", "HSBC Joint", "joint", "current", "hsbc_pdf", "joint/hsbc",
                             "MR A RIVERA & MS S RIVERA", "99-10-20", "12341020", _p(2450.00))
        self.alex = Account("alex-monzo", "Monzo Alex", "alex", "current", "monzo_pdf", "alex/monzo",
                            FULL_NAMES["alex"], "99-30-40", "12343040", _p(412.37),
                            pots=[Pot("Savings", "Savings pot", _p(3200.00), 3.6),
                                  Pot("Rainy day", "Savings pot", _p(1150.00), 3.1)])
        self.sam = Account("sam-monzo", "Monzo Sam", "sam", "current", "monzo_csv", "sam/monzo",
                           FULL_NAMES["sam"], "99-30-41", "12343041", _p(1240.00))
        self.amex = Account("alex-amex", "Amex Alex", "alex", "credit_card", "amex_pdf", "alex/amex",
                            "ALEX RIVERA", "", "xxxx-xxxxxx-00000", -_p(742.18))
        for pot in self.alex.pots:
            pot.balance = pot.opening
        self.amex_owed = -self.amex.opening

    # ----------------------------------------------------------- helpers
    def amt(self, lo: float, hi: float) -> int:
        return _p(self.rng.uniform(lo, hi))

    def days(self, y: int, m: int, weekdays: set[int] | None = None) -> list[date]:
        n = calendar.monthrange(y, m)[1]
        out = [date(y, m, d) for d in range(1, n + 1)]
        return [d for d in out if weekdays is None or d.weekday() in weekdays]

    def pick_days(self, y: int, m: int, k: int, weekdays: set[int] | None = None) -> list[date]:
        pool = self.days(y, m, weekdays)
        return sorted(self.rng.sample(pool, min(k, len(pool))))

    def rate(self, base: float) -> str:
        return f"{base + self.rng.uniform(-0.012, 0.012):.4f}"

    # ------------------------------------------------------------ months
    def run(self) -> Household:
        for i, (y, m) in enumerate(self.months):
            self.month(i, y, m)
        accounts = {a.key: a for a in (self.joint, self.alex, self.sam, self.amex)}
        for a in accounts.values():
            a.txns.sort(key=lambda t: t.day)          # stable: same-day order is the order generated
            for pot in a.pots:
                pot.txns.sort(key=lambda t: t.day)
        return Household(seed=0, reference=self.reference, months=self.months, accounts=accounts)

    def month(self, i: int, y: int, m: int) -> None:
        last = calendar.monthrange(y, m)[1]
        payday = _last_working_day(y, m)
        december, summer, winter = m == 12, m == 8, m in (10, 11, 12, 1, 2, 3)
        holiday = [date(y, 8, d) for d in range(10, 18)] if summer else []
        rng = self.rng

        # Amex first: the joint account pays last month's balance on the 20th.
        payment = self.amex_owed
        self.amex_month(i, y, m, december, summer, payment)

        # ---------------------------------------------------------- joint
        J = self.joint.txns
        J.append(Txn(date(y, m, 1), -_p(640.00), ["LITTLE ACORNS NURSERY", "CHILDCARE FEES"], "SO"))
        J.append(Txn(date(y, m, 1), -_p(700.00), ["A RIVERA", "MONZO SPENDING"], "SO"))
        J.append(Txn(date(y, m, 1), _p(1350.00), ["S RIVERA", "JOINT ACCOUNT"], "CR"))
        J.append(Txn(date(y, m, 2), -_p(1145.00), ["EXAMPLE HOME LOANS", "MTG 12349876"], "DD"))
        J.append(Txn(date(y, m, 2), -_p(200.00), ["EXAMPLE HOME LOANS", "OVERPAYMENT"], "SO"))
        J.append(Txn(date(y, m, 3), -_p(300.00), ["EXAMPLE INVEST ISA", "A RIVERA"], "SO"))
        J.append(Txn(date(y, m, 5), -_p(158.00 if winter else 118.00), ["BRIGHTSPARK ENERGY"], "DD"))
        J.append(Txn(date(y, m, 8), -_p(41.20), ["EXAMPLE WATER"], "DD"))
        if m not in (2, 3):
            J.append(Txn(date(y, m, 10), -_p(182.00), ["EXAMPLETOWN COUNCIL", "CTAX 99001234"], "DD"))
        J.append(Txn(date(y, m, 12), -_p(41.50 if 4 <= m <= 9 else 38.00), ["FIBRELINE BROADBAND"], "DD"))
        J.append(Txn(date(y, m, 15), -_p(23.75), ["HOMESAFE INSURANCE"], "DD"))
        J.append(Txn(date(y, m, 15), -_p(47.90), ["MOTORWISE INSURANCE"], "DD"))
        J.append(Txn(date(y, m, 18), -_p(27.40), ["PAWSURE PET INSURANCE"], "DD"))
        if payment > 0:
            J.append(Txn(date(y, m, 20), -payment, ["AMERICAN EXPRESS", "AMEX 00000"], "DD"))
        J.append(Txn(date(y, m, 22), -_p(16.63), ["DVLA-EX24 DMO"], "DD"))
        J.append(Txn(date(y, m, 25), -_p(36.00), ["SPLASH SWIM SCHOOL"], "DD"))
        salary = 3912.40 if (y, m) < (self.months[5][0], self.months[5][1]) else 4038.15   # pay rise half way
        J.append(Txn(payday, _p(salary), [EMPLOYER, "SALARY"], "CR"))

        for d in self.days(y, m, {5}):                                   # Saturday big shop
            if d in holiday:
                continue
            shop = rng.choice([("TESCO STORES 2041", "EXAMPLETOWN"), ("SAINSBURYS S/MKT", "EXAMPLETOWN"),
                               ("TESCO STORES 2041", "EXAMPLETOWN")])
            amount = self.amt(78, 132) * (135 if december else 100) // 100
            J.append(Txn(d, -amount, list(shop), rng.choice(["VIS", ")))"])))
        for d in self.pick_days(y, m, 2, {1, 3}):                        # mid-week top-up
            if d not in holiday:
                J.append(Txn(d, -self.amt(9, 31), [rng.choice(["ALDI 4471", "LIDL GB EXAMPLETOWN"])], ")))"))
        if december:
            J.append(Txn(date(y, 12, 22), -self.amt(150, 190), ["WAITROSE 0312", "EXAMPLETOWN"], "VIS"))
        for d in self.pick_days(y, m, 2):
            if d not in holiday:
                J.append(Txn(d, -self.amt(54, 74), [rng.choice(["ESSO EXAMPLE ROAD", "SHELL EXAMPLETOWN"])], "VIS"))
        J.append(Txn(self.pick_days(y, m, 1)[0], -_p(rng.choice([40, 50, 60])), ["CASH EXAMPLETOWN HIGH ST"], "ATM"))
        J.append(Txn(self.pick_days(y, m, 1)[0], -self.amt(28, 46), ["PETS AT HOME", "EXAMPLETOWN"], ")))"))
        for d in self.pick_days(y, m, rng.choice([1, 2])):              # no rule: left for Review
            J.append(Txn(d, -self.amt(9, 28), ["HILLTOP FARM SHOP"], ")))"))
        if m in (4, 5, 6):
            J.append(Txn(self.pick_days(y, m, 1)[0], -self.amt(25, 90), ["WHEELERS GARDEN CENTRE"], "VIS"))
            J.append(Txn(self.pick_days(y, m, 1)[0], -self.amt(18, 75), ["B&Q 1187", "EXAMPLETOWN"], "VIS"))
        if m == 5:
            J.append(Txn(date(y, m, 14), _p(86.40), ["BRIGHTSPARK ENERGY", "REFUND"], "CR"))
        if i in (3, 9):
            J.append(Txn(self.pick_days(y, m, 1)[0], -_p(85.00 if i == 3 else 142.50), ["EXAMPLETOWN VETS"], "VIS"))
        if m == 9:
            J.append(Txn(date(y, m, 4), -self.amt(60, 85), ["SCHOOLWEAR EXAMPLETOWN"], "VIS"))
        if summer:
            for d, merchant, eur in ((date(y, 8, 11), "CAFE CENTRAL", 23.80), (date(y, 8, 13), "FARMACIA DEL MAR", 17.45),
                                     (date(y, 8, 15), "CAFE CENTRAL", 31.20)):
                rate = self.rate(1.172)
                gbp = _p(eur / float(rate))
                J.append(Txn(d, -gbp, [merchant, "MALAGA ES", f"EUR {eur:.2f} @ {rate}"], "VIS",
                             fx=("EUR", _p(eur), rate)))
                J.append(Txn(d, -max(1, gbp * 275 // 10000), ["NON-STERLING", "TRANSACTION FEE"], "DR"))

        # ---------------------------------------------------- Alex's Monzo
        self.alex_month(i, y, m, last, december, summer, holiday)
        # ----------------------------------------------------- Sam's Monzo
        self.sam_month(i, y, m, last, payday, december, summer, holiday)

    def amex_month(self, i: int, y: int, m: int, december: bool, summer: bool, payment: int) -> None:
        rng, A = self.rng, self.amex.txns
        before = len(A)
        A.append(Txn(date(y, m, 7), -_p(10.99), ["NETFLIX.COM"], "CARD"))
        A.append(Txn(date(y, m, 14), -_p(8.99), ["DISNEY PLUS"], "CARD"))
        A.append(Txn(date(y, m, 21), -_p(8.99), ["AMAZON PRIME"], "CARD"))
        if payment > 0:
            A.append(Txn(date(y, m, 20), payment, ["PAYMENT RECEIVED - THANK YOU"], "CR"))
        A.append(Txn(self.pick_days(y, m, 1)[0], -self.amt(84, 121), ["OCADO RETAIL LTD"], "CARD"))
        for d in self.pick_days(y, m, rng.randint(2, 4) + (5 if december else 0)):
            A.append(Txn(d, -self.amt(7, 65 if not december else 95), ["AMAZON.CO.UK", "MARKETPLACE"], "CARD"))
        for d in self.pick_days(y, m, 2 + (1 if december else 0), {4, 5, 6}):
            A.append(Txn(d, -self.amt(23, 39), ["DELIVEROO"], "CARD"))
        for d in self.pick_days(y, m, rng.randint(1, 2) + (2 if december else 0), {4, 5}):
            A.append(Txn(d, -self.amt(38, 86), ["WAGAMAMA EXAMPLETOWN"], "CARD"))
        if i % 2 == 0:
            A.append(Txn(self.pick_days(y, m, 1)[0], -self.amt(25, 80), ["NEXT RETAIL"], "CARD"))
        if i % 3 == 1:
            A.append(Txn(self.pick_days(y, m, 1)[0], -self.amt(19, 49), ["UNIQLO EXAMPLETOWN"], "CARD"))
        if rng.random() < 0.5:
            A.append(Txn(self.pick_days(y, m, 1)[0], -self.amt(6, 19), ["BOOTS 0571"], "CARD"))
        if i in (1, 5, 8):
            A.append(Txn(self.pick_days(y, m, 1, set(range(7)))[0], self.amt(9, 34), ["AMAZON.CO.UK", "MARKETPLACE"], "CR"))
        if i == 6:
            A.append(Txn(date(y, m, 9), _p(32.00), ["NEXT RETAIL"], "CR"))
        if december:
            A.append(Txn(date(y, 12, 6), -self.amt(140, 220), ["JOHN LEWIS", "EXAMPLETOWN"], "CARD"))
            A.append(Txn(date(y, 12, 13), -self.amt(60, 110), ["SMYTHS TOYS"], "CARD"))
            A.append(Txn(date(y, 12, 18), -self.amt(45, 80), ["SMYTHS TOYS"], "CARD"))
        elif rng.random() < 0.3:
            A.append(Txn(self.pick_days(y, m, 1)[0], -self.amt(30, 120), ["JOHN LEWIS", "EXAMPLETOWN"], "CARD"))
        if m == 2:
            A.append(Txn(date(y, 2, 9), -_p(412.36), ["EASYJET", "LUTON"], "CARD"))
        if m == 4:
            A.append(Txn(date(y, 4, 3), -_p(345.00), ["WOODLAND LODGES"], "CARD"))   # no rule: left for Review
        if i == 4:                                                                    # the one-off
            A.append(Txn(date(y, m, 11), -_p(1236.40), ["MOTORCARE EXAMPLETOWN"], "CARD", tag=CAR_REPAIR_TAG))
        if i == 10:
            A.append(Txn(date(y, m, 6), -_p(189.00), ["MOTORCARE EXAMPLETOWN"], "CARD"))
        if summer:
            rate = self.rate(1.165)
            eur = 980.00
            base = _p(eur / float(rate))
            fee = base * 299 // 10000
            A.append(Txn(date(y, 8, 17), -(base + fee), ["HOTEL MIRAMAR", "MALAGA"], "CARD",
                         fx=("EUR", _p(eur), rate), fee_pence=fee))
        new = A[before:]
        self.amex_owed += -sum(t.pence for t in new)

    def alex_month(self, i, y, m, last, december, summer, holiday) -> None:
        rng, M = self.rng, self.alex.txns
        savings, rainy = self.alex.pots
        M.append(Txn(date(y, m, 1), _p(700.00), ["A & S RIVERA (Faster Payments)", "Reference: MONZO SPENDING"]))
        for pot, amount in ((savings, _p(300.00)), (rainy, _p(50.00))):
            M.append(Txn(date(y, m, 2), -amount, [f"Transfer to Pot: {pot.name}"]))
            pot.txns.append(Txn(date(y, m, 2), amount, ["Transfer from Personal Account"]))
            pot.balance += amount
        if december:
            M.append(Txn(date(y, 12, 9), _p(250.00), ["Transfer from Pot: Rainy day"]))
            rainy.txns.append(Txn(date(y, 12, 9), -_p(250.00), ["Transfer to Personal Account"]))
            rainy.balance -= _p(250.00)
        M.append(Txn(date(y, m, 9), -_p(11.99), ["SPOTIFY"]))
        weekdays = [d for d in self.days(y, m, {0, 1, 2, 3, 4}) if d not in holiday]
        for d in sorted(rng.sample(weekdays, min(len(weekdays), rng.randint(8, 12)))):
            M.append(Txn(d, -self.amt(2.95, 4.20), ["COSTA COFFEE EXAMPLETOWN GBR"]))
        for d in sorted(rng.sample(weekdays, min(len(weekdays), rng.randint(3, 5)))):
            M.append(Txn(d, -self.amt(6.10, 9.40), [rng.choice(["PRET A MANGER EXAMPLETOWN GBR", "GREGGS EXAMPLETOWN GBR"])]))
        for d in self.pick_days(y, m, rng.randint(1, 3) + (2 if december else 0), {3, 4, 5}):
            M.append(Txn(d, -self.amt(9, 42), ["THE OLD BELL EXAMPLETOWN GBR"]))        # no rule
        for d in self.pick_days(y, m, rng.randint(0, 2)):
            M.append(Txn(d, -self.amt(18, 64), ["TRAINLINE.COM LONDON GBR"]))
        if i % 2 == 1:
            M.append(Txn(self.pick_days(y, m, 1, {5})[0], -_p(18.00), ["BARBER & CO EXAMPLETOWN GBR"]))  # no rule
        M.append(Txn(self.pick_days(y, m, 1)[0], -self.amt(6, 14), ["SQ *COPPER KETTLE CAFE EXAMPLETOWN GBR"]))  # no rule
        if december:
            M.append(Txn(date(y, 12, 12), -self.amt(35, 60), ["WATERSTONES EXAMPLETOWN GBR"]))
        if summer:
            for d, merchant, eur in ((date(y, 8, 10), "MERCADONA MALAGA ESP", 64.30), (date(y, 8, 12), "CHIRINGUITO EL FARO MALAGA ESP", 48.50),
                                     (date(y, 8, 14), "MERCADONA MALAGA ESP", 41.75), (date(y, 8, 16), "TAXI MALAGA ESP", 22.00)):
                rate = self.rate(1.168)
                M.append(Txn(d, -_p(eur / float(rate)), [merchant, f"Amount: EUR -{eur:.2f}. Exchange rate: {rate}."],
                             fx=("EUR", _p(eur), rate)))
        for pot in (savings, rainy):
            interest = round(pot.balance * pot.rate / 100 / 12)
            pot.txns.append(Txn(date(y, m, last), interest, [f"Interest for {calendar.month_name[m]} {y}"]))
            pot.balance += interest

    def sam_month(self, i, y, m, last, payday, december, summer, holiday) -> None:
        rng, S = self.rng, self.sam.txns

        def add(d, pounds_pence, kind, name, category, description="", fx=None):
            S.append(Txn(d, pounds_pence, [name, description] if description else [name], kind, fx=fx, category=category))

        add(date(y, m, 1), -_p(1350.00), "Faster payment", "A & S Rivera", "Transfers", "JOINT ACCOUNT")
        add(date(y, m, 2), -_p(500.00), "Faster payment", "Example Savings ISA", "Savings", "S RIVERA ISA")
        add(date(y, m, 3), -_p(34.00), "Direct Debit", "FlexFit Gym", "Personal care")
        add(date(y, m, 6), -_p(22.00), "Direct Debit", "Skyline Mobile", "Bills")
        add(date(y, m, 11), -_p(2.99), "Card payment", "Apple.com/bill", "Entertainment", "APPLE.COM/BILL")
        add(payday, _p(2684.20), "Bacs (Direct Credit)", SAM_EMPLOYER.title(), "Income", "SALARY")
        weekdays = [d for d in self.days(y, m, {0, 1, 2, 3, 4}) if d not in holiday]
        for d in sorted(rng.sample(weekdays, min(len(weekdays), rng.randint(6, 9)))):
            name, desc = rng.choice([("Costa Coffee", "COSTA COFFEE EXAMPLETOWN"), ("Greggs", "GREGGS EXAMPLETOWN")])
            add(d, -self.amt(2.60, 4.10), "Card payment", name, "Eating out", desc)
        for d in self.days(y, m, {2}):
            if d not in holiday:
                add(d, -self.amt(9, 26), "Card payment", "Lidl", "Groceries", "LIDL GB EXAMPLETOWN")
        for d in self.days(y, m, {4}):
            if d not in holiday:
                add(d, -self.amt(5, 15), "Card payment", "Co-op", "Groceries", "CO-OP GROUP 070512")
        for d in self.pick_days(y, m, 2, {0, 1, 2, 3, 4}):
            add(d, -self.amt(16, 41), "Card payment", "Trainline", "Transport", "TRAINLINE.COM")
        add(date(y, m, 4), -_p(15.00), "Card payment", "SumUp *EXFC Juniors", "Family", "SUMUP *EXFC JUNIORS")   # no rule
        add(self.pick_days(y, m, 1, {5})[0], -self.amt(8, 20), "Card payment", "Zettle_*Riverside Mkt", "General",
            "ZETTLE_*RIVERSIDE MKT")                                                                         # no rule
        add(self.pick_days(y, m, 1)[0], -self.amt(6, 18), "Card payment", "Boots", "Personal care", "BOOTS 1123")
        if i % 3 == 2:
            add(self.pick_days(y, m, 1)[0], -self.amt(20, 55), "Card payment", "Uniqlo", "Shopping", "UNIQLO EXAMPLETOWN")
        if i in (2, 8):
            add(self.pick_days(y, m, 1, {1, 2, 3})[0], -_p(65.00), "Card payment", "Exampletown Dental", "Personal care",
                "EXAMPLETOWN DENTAL")
        if december:
            add(date(y, 12, 10), -self.amt(25, 45), "Card payment", "Waterstones", "Gifts", "WATERSTONES EXAMPLETOWN")
            add(date(y, 12, 16), -self.amt(40, 70), "Card payment", "The Old Bell", "Eating out", "THE OLD BELL")
        if summer:
            for d, name, eur in ((date(y, 8, 11), "Heladeria La Playa", 9.40), (date(y, 8, 13), "Mercadona", 37.85),
                                 (date(y, 8, 16), "Museo Picasso Malaga", 24.00)):
                rate = self.rate(1.168)
                add(d, -_p(eur / float(rate)), "Card payment", name, "Holidays", name.upper() + " MALAGA ESP",
                    fx=("EUR", _p(eur), rate))
        if i == len(self.months) - 1:                     # the export runs to the last day of the year
            add(date(y, m, last), -_p(3.45), "Card payment", "Costa Coffee", "Eating out", "COSTA COFFEE EXAMPLETOWN")


def generate(seed: int = 42, reference: date | None = None) -> Household:
    """The household for `seed`, with its year ending in the last complete month before `reference` (default today)."""
    hh = _Gen(seed, reference or date.today()).run()
    hh.seed = seed
    return hh
