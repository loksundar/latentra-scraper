"""
Salary extraction from description text.

Most US postings state pay in the description (pay-transparency laws) while the
ATS API's salary fields stay empty. On a 6.7k-job sample this filled 70% of
jobs that had no structured salary. Annual USD only; anything implausible
(hourly rates, equity, company valuations) is rejected.
"""

import html
import re

_NUM = r"(\d{2,3}(?:,\d{3})+(?:\.\d{2})?|\d{2,3}(?:\.\d+)?\s?[kK])"
# A "$" that is not the tail of another currency's symbol: "CA$", "C$", "A$",
# "AU$", "NZ$", "HK$", "S$", "SG$", "MX$", "NT$", "R$" are not US dollars. A
# bare "$", "US$" or "USD$" is. (Greenhouse strips tags into "Pay Range$201,800",
# so a plain "no letter before $" rule would miss real US ranges.)
_DOLLAR = (r"(?<!\bCA)(?<!\bC)(?<!\bA)(?<!\bAU)(?<!\bNZ)(?<!\bHK)(?<!\bS)(?<!\bSG)"
           r"(?<!\bMX)(?<!\bNT)(?<!\bR)\$")
# "$150,000 - $210,000", "$150K to $210K", "$182,000—$250,208 USD", "US$120,000 - US$160,000"
_RANGE = re.compile(
    r"(?:\bUSD?\s*)?" + _DOLLAR + r"\s?" + _NUM
    + r"\s*(?:USD\s*)?(?:-|–|—|to)\s*(?:USD?\s*)?(?:" + _DOLLAR + r")?\s?" + _NUM
)
# Text right after a matched range that says it is NOT an annual USD figure:
# another currency, or an hourly / weekly / monthly rate.
_NOT_ANNUAL_USD = re.compile(
    r"^\s*(?:USD\s*)?(?:"
    r"\b(?:CAD|AUD|EUR|GBP|INR|SGD|NZD|CHF|MXN|BRL|JPY|HKD|SEK|DKK|NOK|PLN|ILS|ZAR)\b"
    r"|(?:per|/|an?)\s*(?:hour|hr|week|wk|month|mo)\b|hourly|weekly|monthly"
    r")",
    re.I,
)
# Amazon listing line: "USA, WA, Seattle - 130,400.00 - 176,300.00 USD annually"
_AMAZON = re.compile(r"(\d{2,3},\d{3})(?:\.\d{2})?\s*-\s*(\d{2,3},\d{3})(?:\.\d{2})?\s*USD annually")
# "from $129,300/year in our lowest geographic market up to $223,600/year"
_FROM_UPTO = re.compile(r"\$(\d{2,3},\d{3})(?:\.\d{2})?\s*/\s*year.{0,200}?up to \$(\d{2,3},\d{3})(?:\.\d{2})?\s*/\s*year", re.S)


def _to_int(s: str) -> int:
    s = s.replace(",", "").replace(" ", "")
    if s[-1:] in ("k", "K"):
        return int(round(float(s[:-1]) * 1000))
    return int(round(float(s)))


def _plausible(lo: int, hi: int) -> bool:
    return 30_000 <= lo <= hi <= 900_000 and hi <= lo * 3


def extract_salary(text: str):
    """Return (min, max) annual USD from free text, or None."""
    if not text:
        return None
    t = html.unescape(html.unescape(text)).replace(" ", " ")
    for rx in (_AMAZON, _FROM_UPTO):
        m = rx.search(t)
        if m:
            lo, hi = sorted((_to_int(m.group(1)), _to_int(m.group(2))))
            if _plausible(lo, hi):
                return lo, hi
    for m in _RANGE.finditer(t):
        try:
            lo, hi = sorted((_to_int(m.group(1)), _to_int(m.group(2))))
        except ValueError:
            continue
        if _NOT_ANNUAL_USD.search(t[m.end():m.end() + 24]):
            continue   # "$80,000 - $95,000 CAD" / "... per month" — not annual USD
        if _plausible(lo, hi):
            return lo, hi
    return None
