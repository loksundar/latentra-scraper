"""
Link classifier — detects ATS type and company slug from a careers page URL.

Supports: Greenhouse, Lever, Workday, Ashby, SmartRecruiters
Returns: {"source_type": "...", "company_slug": "...", "base_url": "..."} or None
"""

import re
from urllib.parse import urlparse


# Each pattern: (regex, source_type, slug_group_index, base_url_builder)
# base_url_builder: callable(match) -> base_url or None
PATTERNS = [
    # ── Greenhouse ──────────────────────────────────
    # https://boards.greenhouse.io/company
    # https://boards-api.greenhouse.io/v1/boards/company/jobs
    # https://job-boards.greenhouse.io/company
    (
        r'(?:boards(?:-api)?|job-boards)\.greenhouse\.io/(?:v1/boards/)?([a-z0-9_-]+)',
        'greenhouse',
        1,
        None,  # standard URL, no base_url needed
    ),

    # ── Lever ───────────────────────────────────────
    # https://jobs.lever.co/company
    # https://api.lever.co/v0/postings/company
    (
        r'(?:jobs|api)\.lever\.co/(?:v0/postings/)?([a-z0-9_-]+)',
        'lever',
        1,
        None,
    ),

    # ── Ashby ───────────────────────────────────────
    # https://jobs.ashbyhq.com/company
    # https://api.ashbyhq.com/posting-api/job-board/company
    (
        r'(?:jobs|api)\.ashbyhq\.com/(?:posting-api/job-board/)?([a-z0-9_-]+)',
        'ashby',
        1,
        None,
    ),

    # ── Workday ─────────────────────────────────────
    # https://company.wd5.myworkdayjobs.com/en-US/SiteId/...
    # https://company.wd5.myworkdayjobs.com/SiteId
    # Custom domains: https://careers.company.com/... with /wday/cxs/tenant/site
    (
        r'([a-z0-9_-]+)\.wd(\d+)\.myworkdayjobs\.com/(?:en-US/)?([a-z0-9_-]+)',
        'workday',
        1,  # tenant name as slug
        lambda m: f"https://{m.group(1)}.wd{m.group(2)}.myworkdayjobs.com/wday/cxs/{m.group(1)}/{m.group(3)}",
    ),
    # Custom domain with /wday/cxs/ path
    (
        r'(https?://[^/]+)/wday/cxs/([a-z0-9_-]+)/([a-z0-9_-]+)',
        'workday',
        2,  # tenant name
        lambda m: f"{m.group(1)}/wday/cxs/{m.group(2)}/{m.group(3)}",
    ),

    # ── SmartRecruiters ─────────────────────────────
    # https://jobs.smartrecruiters.com/Company
    # https://careers.smartrecruiters.com/Company
    (
        r'(?:jobs|careers)\.smartrecruiters\.com/([a-zA-Z0-9_-]+)',
        'smartrecruiters',
        1,
        None,
    ),
]

# Compile patterns once
_COMPILED = [(re.compile(p, re.IGNORECASE), stype, gidx, builder) for p, stype, gidx, builder in PATTERNS]


def classify_url(url: str) -> dict | None:
    """
    Classify a careers page URL into its ATS source type and company slug.

    Returns:
        {"source_type": str, "company_slug": str, "base_url": str|None}
        or None if unrecognized.
    """
    url = url.strip()
    if not url:
        return None

    for regex, source_type, group_idx, builder in _COMPILED:
        match = regex.search(url)
        if match:
            slug = match.group(group_idx).lower().rstrip("/")
            base_url = builder(match) if builder else None
            return {
                "source_type": source_type,
                "company_slug": slug,
                "base_url": base_url,
            }

    return None


def classify_urls(urls: list[str]) -> list[dict]:
    """
    Classify multiple URLs. Returns list of results (skips unrecognized).
    Each result includes the original URL.
    """
    results = []
    seen = set()  # deduplicate by (source_type, slug)
    for url in urls:
        result = classify_url(url)
        if result:
            key = (result["source_type"], result["company_slug"])
            if key not in seen:
                seen.add(key)
                result["original_url"] = url
                results.append(result)
    return results
