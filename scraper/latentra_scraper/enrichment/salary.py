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
# "$150,000 - $210,000", "$150K to $210K", "$182,000—$250,208 USD"
_RANGE = re.compile(r"\$\s?" + _NUM + r"\s*(?:USD\s*)?(?:-|–|—|to)\s*(?:USD\s*)?\$?\s?" + _NUM)
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
        if _plausible(lo, hi):
            return lo, hi
    return None
