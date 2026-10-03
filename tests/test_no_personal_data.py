"""No personal data in the repository.

Scans every text file git would publish (tracked files, plus untracked files that are not ignored, so a new file
is checked before it is added) for:
  * UK sort code and account number pairs outside the demo's fake ranges (sort codes 99-xx-xx, eight-digit
    account numbers starting 1234);
  * IBANs (anything shaped like one whose check digits are valid);
  * UK postcodes other than the demo's EX1 1AA;
  * email addresses other than example.com / example.org ones and noreply addresses.

It also reads pii.local.txt at the repository root, if present: one literal string per line (blank lines and lines
starting with # are ignored), matched case-insensitively. The file is gitignored; put your own names, account
numbers, employer and address in it so a stray copy of any of them fails the suite.

Skipped: src/slopfi/web/static/vendor (third-party bundles), binary files, and files deleted from the work tree.
Sample values below are assembled at run time so this file does not trip its own checks.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PII_FILE = ROOT / "pii.local.txt"
SKIP_DIRS = ("src/slopfi/web/static/vendor/",)
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".db", ".sqlite", ".zip", ".gz",
                   ".woff", ".woff2", ".ttf", ".otf"}

FAKE_SORT_CODE_PREFIX = "99"
FAKE_ACCOUNT_PREFIX = "1234"
FAKE_POSTCODE = "EX1 1AA"
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org")

# Shapes caught: "99-10-20 12341020", "991020 12341020", "Sort code 99-10-20, Account number 12341020"
SORT_ACCOUNT_RE = re.compile(
    r"(?<![\w-])(?P<sort>\d{2}-\d{2}-\d{2}|\d{6})(?![\w-])"
    r"[\s,;:/|()]*(?:(?:account|acct|a/c)(?:\s*(?:number|no\.?))?[\s:]*)?"
    r"(?<!\d)(?P<account>\d{8})(?![\w-])", re.I)
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
POSTCODE_RE = re.compile(r"\b(?:[A-PR-UWYZ][A-HK-Y]?\d[A-Z\d]?) ?\d[ABD-HJLNP-UW-Z]{2}\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


# ------------------------------------------------------------------ files
def _git_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:      # an unpacked sdist: nothing to publish from here
        pytest.skip(f"not a git checkout ({exc})")
    return sorted({p for p in out.decode().split("\0") if p})


def _text(rel: str) -> str | None:
    path = ROOT / rel
    if rel.startswith(SKIP_DIRS) or path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
        return None
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


@pytest.fixture(scope="module")
def files() -> dict[str, str]:
    texts = {rel: _text(rel) for rel in _git_files()}
    found = {rel: t for rel, t in texts.items() if t is not None}
    assert "tests/test_no_personal_data.py" in found and "pyproject.toml" in found, "the scan found no files"
    return found


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


# --------------------------------------------------------------- detectors
def sort_code_pairs(text: str) -> list[tuple[int, str]]:
    hits = []
    for m in SORT_ACCOUNT_RE.finditer(text):
        sort, account = m["sort"].replace("-", ""), m["account"]
        if not (sort.startswith(FAKE_SORT_CODE_PREFIX) and account.startswith(FAKE_ACCOUNT_PREFIX)):
            hits.append((m.start(), m.group(0)))
    return hits


def _iban_valid(candidate: str) -> bool:
    s = candidate.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    digits = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(digits) % 97 == 1


def ibans(text: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)) for m in IBAN_RE.finditer(text) if _iban_valid(m.group(0))]


def postcodes(text: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)) for m in POSTCODE_RE.finditer(text)
            if re.sub(r"\s", "", m.group(0)) != FAKE_POSTCODE.replace(" ", "")]


def emails(text: str) -> list[tuple[int, str]]:
    hits = []
    for m in EMAIL_RE.finditer(text):
        local, domain = m.group(0).lower().rsplit("@", 1)
        if any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS):
            continue
        if re.fullmatch(r"no-?reply(\+.*)?", local):
            continue
        hits.append((m.start(), m.group(0)))
    return hits


def _report(files: dict[str, str], detector) -> list[str]:
    return [f"{rel}:{_line(text, pos)}: {value}" for rel, text in files.items() for pos, value in detector(text)]


# -------------------------------------------------------------------- scan
def test_no_real_sort_code_and_account_number_pairs(files):
    assert not _report(files, sort_code_pairs)


def test_no_ibans(files):
    assert not _report(files, ibans)


def test_no_postcodes_but_the_demo_one(files):
    assert not _report(files, postcodes)


def test_no_email_addresses_but_example_and_noreply(files):
    assert not _report(files, emails)


def _local_strings() -> list[str]:
    if not PII_FILE.exists():
        return []
    lines = (line.strip() for line in PII_FILE.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def test_no_string_from_pii_local_txt(files):
    """Each line of the gitignored pii.local.txt is a literal that must appear in no published file."""
    needles = [(s, s.casefold()) for s in _local_strings()]
    hits = [f"{rel}:{_line(text, text.casefold().find(folded))}: {needle!r}"
            for rel, text in files.items() for needle, folded in needles if folded in text.casefold()]
    assert not hits


def test_pii_local_txt_is_never_published():
    ignored = subprocess.run(["git", "check-ignore", "-q", "pii.local.txt"], cwd=ROOT).returncode == 0
    assert ignored, "add pii.local.txt to .gitignore"
    assert "pii.local.txt" not in _git_files()


# --------------------------------------------------- the detectors work
def _j(*parts: str, sep: str = "") -> str:
    return sep.join(parts)


def test_detectors_catch_what_they_should_and_pass_the_fakes():
    real_sort, real_account = _j("40", "12", "34", sep="-"), _j("8765", "4321")
    assert sort_code_pairs(f"{real_sort} {real_account}")
    assert sort_code_pairs(f"Sort code: {real_sort}, Account number: {real_account}")
    assert sort_code_pairs(f"{real_sort.replace('-', '')} {real_account}")
    assert sort_code_pairs(f"{_j('99', '10', '20', sep='-')} {real_account}")            # fake sort, real account
    assert not sort_code_pairs("99-10-20 12341020") and not sort_code_pairs("99-30-40 12343040 pot:Savings")
    assert not sort_code_pairs("2026-06-26 12:00") and not sort_code_pairs("MTG 12349876")

    assert ibans(_j("GB82", " WEST", " 1234", " 5698", " 7654", " 32"))                       # the textbook example
    assert ibans(_j("GB82", "WEST", "1234", "5698", "7654", "32"))
    assert not ibans("GB00 WEST 1234 5698 7654 32")                                           # bad check digits

    assert postcodes(_j("BS1 ", "4DJ")) and postcodes(_j("SW1A ", "1AA")) and postcodes(_j("M1 ", "1AE"))
    assert not postcodes("1 Example Street, Exampletown, EX1 1AA") and not postcodes("EX12AB34C")

    assert emails(_j("someone", "@", "gmail.com"))
    assert not emails("alex@example.com") and not emails("sam@mail.example.org")
    assert not emails("noreply@anthropic.com") and not emails("no-reply@github.com")


def test_pii_local_lines_are_literals_matched_case_insensitively(tmp_path, monkeypatch):
    local = tmp_path / "pii.local.txt"
    local.write_text("# my own details\n\nJo Bloggs\n  Bloggs & Co (UK)  \n", encoding="utf-8")
    monkeypatch.setitem(globals(), "PII_FILE", local)
    assert _local_strings() == ["Jo Bloggs", "Bloggs & Co (UK)"]
    published = {"notes.md": "paid BLOGGS & CO (UK) on Friday", "other.py": "nothing here"}
    with pytest.raises(AssertionError):
        test_no_string_from_pii_local_txt(published)
    test_no_string_from_pii_local_txt({"other.py": "Bloggs and Co"})
