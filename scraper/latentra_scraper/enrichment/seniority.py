"""
Seniority inference — determines job level from title and description.
Uses title keyword matching + years-of-experience extraction from description.
"""

import re

# "Manager" in these job titles names the FUNCTION, not a people-management
# level: a Product Manager, Technical Program Manager or Account Manager is an
# individual contributor. They are blanked out before the title rules run, so
# "Senior Product Manager" → senior and "Product Manager Intern" → intern, while
# "Engineering Manager" / "Manager, Product Management" still → manager.
_FUNCTION_MANAGER = re.compile(
    r"\b(?:technical\s+account|customer\s+success|product\s+marketing|business\s+development|"
    r"product|programs?|programme|projects?|accounts?|success|community|content|marketing|brand|"
    r"partner(?:ship)?s?|channel|relationship|portfolio|supply|sourcing|vendor|office|case|release|"
    r"change|incident|risk|compliance|facilities|events?|category|campaign|growth|engagement|delivery|"
    r"solutions?|sales|territory|regional\s+sales|revenue|escalation|implementation|onboarding|"
    r"enablement|operations|ops|data|fraud|payroll|benefits|hr|people)\s+manager\b",
    re.IGNORECASE,
)

# Title-based seniority patterns (order matters — check specific before generic)
TITLE_PATTERNS = [
    # An intern is an intern whatever else the title says ("Product Manager Intern").
    (r'\b(intern|internship|co-op|coop|apprentice)\b', 'intern'),
    (r'\b(vp|vice president|svp|evp)\b', 'vp'),
    (r'\b(director)\b', 'director'),
    (r'\b(c-level|cto|cfo|ceo|coo|ciso|cio|chief)\b', 'executive'),
    (r'\b(head of)\b', 'manager'),
    # "Senior Manager, X" / "Sr. Engineering Manager" manage people → manager, not senior.
    (r'\b(?:senior|sr\.?)\s+(?:\w+\s+){0,2}manager\b', 'manager'),
    (r'\b(principal|distinguished|fellow)\b', 'lead'),
    (r'\b(staff)\b', 'lead'),
    (r'\b(senior|sr\.?)\b', 'senior'),
    (r'\b(lead|tech lead|team lead)\b', 'lead'),
    (r'\b(manager)\b', 'manager'),
    (r'\b(junior|jr\.?|associate|entry[- ]?level|early[- ]career)\b', 'entry'),
    (r'\b(new grad|new graduate|recent graduate|graduate|university grad|campus)\b', 'entry'),
    # Numbered levels after a role noun: "Software Engineer I/II/III" (Amazon,
    # Workday tenants). Lowest priority: only when nothing above matched.
    (r'\b(?:engineer|developer|analyst|scientist|administrator|architect|specialist|technician|designer|consultant|programmer|sde|swe)\s*,?\s+(?:i|1)\b(?!\+)', 'entry'),
    (r'\b(?:engineer|developer|analyst|scientist|administrator|architect|specialist|technician|designer|consultant|programmer|sde|swe)\s*,?\s+(?:ii|2)\b', 'mid'),
    (r'\b(?:engineer|developer|analyst|scientist|administrator|architect|specialist|technician|designer|consultant|programmer|sde|swe)\s*,?\s+(?:iii|3)\b', 'senior'),
    (r'\b(?:engineer|developer|analyst|scientist|administrator|architect|specialist|technician|designer|consultant|programmer|sde|swe)\s*,?\s+(?:iv|v|4|5)\b', 'lead'),
]

# Compiled title patterns
_TITLE_COMPILED = [(re.compile(p, re.IGNORECASE), level) for p, level in TITLE_PATTERNS]

# Years-of-experience patterns for description
# Matches: "5+ years", "3-5 years", "at least 7 years", "minimum 3 years"
_YEARS_PATTERNS = [
    re.compile(r'(\d{1,2})\s*\+?\s*(?:to|-|–)\s*(\d{1,2})\s*(?:\+\s*)?years?\b', re.IGNORECASE),
    re.compile(r'(\d{1,2})\s*\+\s*years?\b', re.IGNORECASE),
    re.compile(r'(?:at least|minimum|min\.?|over)\s*(\d{1,2})\s*years?\b', re.IGNORECASE),
    re.compile(r'(\d{1,2})\s*years?\s*(?:of\s+)?(?:experience|exp\.?)\b', re.IGNORECASE),
]

# Map years → seniority
def _years_to_seniority(years_min: int) -> str:
    # "0-2 years" is an entry-level ask, not an internship.
    if years_min <= 1:
        return "entry"
    elif years_min <= 3:
        return "mid"
    elif years_min <= 6:
        return "senior"
    else:
        # Years alone never imply people management: "12+ years" on an
        # architect / specialist posting is a principal-level IC, not a director.
        return "lead"


def infer_seniority(title: str, description_text: str = "") -> dict:
    """
    Infer seniority from title and description.

    Returns:
        {
            "seniority": str,
            "seniority_confidence": str,  # "title" | "description" | "years" | "default"
            "years_experience_min": int|None,
            "years_experience_max": int|None,
        }
    """
    result = {
        "seniority": "mid",
        "seniority_confidence": "default",
        "years_experience_min": None,
        "years_experience_max": None,
    }

    # 1. Try title first (highest confidence)
    title_lower = _FUNCTION_MANAGER.sub(" ", (title or "").lower())
    for pattern, level in _TITLE_COMPILED:
        if pattern.search(title_lower):
            result["seniority"] = level
            result["seniority_confidence"] = "title"
            break

    # 2. Extract years of experience from description
    if description_text:
        years_min, years_max = _extract_years(description_text)
        if years_min is not None:
            result["years_experience_min"] = years_min
            result["years_experience_max"] = years_max

            # Only override seniority if title didn't match
            if result["seniority_confidence"] == "default":
                result["seniority"] = _years_to_seniority(years_min)
                result["seniority_confidence"] = "years"

    return result


def _extract_years(text: str) -> tuple[int | None, int | None]:
    """
    Extract min/max years of experience from description text.

    The FIRST requirement stated wins. Postings list the core ask first
    ("5+ years building distributed systems") and secondary ones after
    ("1+ years with Kubernetes"); taking the smallest number over the whole
    text turned Amazon SDE II roles into "entry".
    """
    found = []   # (position, min, max)

    for pattern in _YEARS_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groups()
            if len(groups) == 2 and groups[1]:
                # Range: "3-5 years"
                try:
                    mn, mx = int(groups[0]), int(groups[1])
                    if mn <= mx <= 30:  # sanity check
                        found.append((match.start(), mn, mx))
                except ValueError:
                    pass
            elif len(groups) >= 1:
                # Single: "5+ years"
                try:
                    val = int(groups[0])
                    if val <= 30:
                        found.append((match.start(), val, None))
                except ValueError:
                    pass

    if not found:
        return None, None

    found.sort()
    years_min = found[0][1]
    maxs = [mx for _, _, mx in found if mx is not None]
    years_max = max(maxs) if maxs else None
    if years_max is not None and years_max < years_min:
        years_max = None
    return years_min, years_max
