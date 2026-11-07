"""
Client-side view of the `jobs` table contract (scraper/schema.sql + migrations
003/006 on the site). jobs-ingest.php binds every field straight into the
INSERT, so whatever does not fit the column is either silently truncated /
coerced (non-strict sql_mode) or fails the whole company transaction (strict
mode) — one 415-character Greenhouse location string would then lose the
company's entire batch. conform_item() makes items fit before they are sent.
"""

# VARCHAR(n) columns — truncate (the migration widened location_city to 200,
# schema.sql still says 100; the smaller bound is the safe one).
MAX_LEN = {
    "title": 300,
    "company": 200,
    "company_slug": 100,
    "department": 200,
    "location": 300,
    "location_city": 100,
    "location_state": 100,
    "location_country": 100,
    "seniority_confidence": 20,
}

# Columns whose value cannot be shortened without changing its meaning. The
# validator drops items that exceed them (a truncated apply_url is a broken link;
# a truncated source_job_id is a different job).
MAX_LEN_IDENTITY = {
    "source_job_id": 100,
    "apply_url": 500,
    "source": 20,
}

ENUMS = {
    "seniority": {"intern", "entry", "mid", "senior", "lead", "manager", "director", "vp", "executive", "unknown"},
    "remote_type": {"onsite", "remote", "hybrid", "unknown"},
    "employment_type": {"full_time", "part_time", "contract", "intern", "unknown"},
}

# TINYINT UNSIGNED (migration 003) / INT UNSIGNED / TINYINT UNSIGNED
INT_RANGES = {
    "years_experience_min": (0, 255),
    "years_experience_max": (0, 255),
    "salary_min": (0, 4_294_967_295),
    "salary_max": (0, 4_294_967_295),
    "quality_score": (0, 100),
    "description_word_count": (0, 4_294_967_295),
}

BOOL_FIELDS = ("is_remote_friendly", "is_multi_cloud", "is_platform_heavy")


def _to_int(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        try:
            return int(round(float(s)))
        except ValueError:
            return None
    return None


def conform_item(item) -> list[str]:
    """
    Coerce an enriched item into the column types/lengths jobs-ingest.php
    binds. Mutates `item` in place and returns a list of human-readable notes
    for every value it had to change (empty when the item already fit).
    """
    notes = []

    for field, limit in MAX_LEN.items():
        value = item.get(field)
        if isinstance(value, str) and len(value) > limit:
            item[field] = value[:limit].rstrip()
            notes.append(f"{field} truncated {len(value)}->{limit}")

    for field, allowed in ENUMS.items():
        value = item.get(field)
        if value not in allowed:
            notes.append(f"{field}={value!r} not in enum -> unknown")
            item[field] = "unknown"

    for field, (lo, hi) in INT_RANGES.items():
        value = item.get(field)
        if value is None:
            continue
        as_int = _to_int(value)
        if as_int is None or as_int < lo or as_int > hi:
            notes.append(f"{field}={value!r} out of range -> NULL")
            item[field] = None
        elif as_int != value:
            item[field] = as_int

    # PDO binds PHP false as "" — an integer column rejects that in strict mode
    # and stores 0 with a warning otherwise. Send real 0/1 instead.
    for field in BOOL_FIELDS:
        value = item.get(field)
        item[field] = 1 if value else 0

    # salary_currency CHAR(3): "US Dollar" / "us$" would be truncated to junk.
    cur = item.get("salary_currency")
    if cur is not None:
        cur_s = str(cur).strip().upper()
        if len(cur_s) == 3 and cur_s.isalpha():
            item["salary_currency"] = cur_s
        else:
            notes.append(f"salary_currency={cur!r} invalid -> NULL")
            item["salary_currency"] = None

    smin, smax = item.get("salary_min"), item.get("salary_max")
    if smin is not None and smax is not None and smin > smax:
        item["salary_min"], item["salary_max"] = smax, smin
        notes.append("salary_min/max swapped")

    ymin, ymax = item.get("years_experience_min"), item.get("years_experience_max")
    if ymin is not None and ymax is not None and ymax < ymin:
        item["years_experience_max"] = None
        notes.append("years_experience_max < min -> NULL")

    return notes
