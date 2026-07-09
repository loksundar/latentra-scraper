"""
Seniority inference — determines job level from title and description.
Uses title keyword matching + years-of-experience extraction from description.
"""

import re

# Title-based seniority patterns (order matters — check specific before generic)
TITLE_PATTERNS = [
    (r'\b(vp|vice president)\b', 'vp'),
    (r'\b(director)\b', 'director'),
    (r'\b(c-level|cto|cfo|ceo|coo|chief)\b', 'executive'),
    (r'\b(head of)\b', 'manager'),
    (r'\b(principal)\b', 'lead'),
    (r'\b(staff)\b', 'lead'),
    (r'\b(senior|sr\.?)\b', 'senior'),
    (r'\b(lead|tech lead|team lead)\b', 'lead'),
    (r'\b(manager)\b', 'manager'),
    (r'\b(junior|jr\.?|associate|entry[- ]?level)\b', 'entry'),
    (r'\b(new grad|new graduate|recent graduate)\b', 'entry'),
    (r'\b(intern|internship|co-op|coop)\b', 'intern'),
]

# Compiled title patterns
_TITLE_COMPILED = [(re.compile(p, re.IGNORECASE), level) for p, level in TITLE_PATTERNS]

# Years-of-experience patterns for description
# Matches: "5+ years", "3-5 years", "at least 7 years", "minimum 3 years"
_YEARS_PATTERNS = [
    re.compile(r'(\d{1,2})\s*\+?\s*(?:to|-)\s*(\d{1,2})\s*(?:\+\s*)?years?\b', re.IGNORECASE),
    re.compile(r'(\d{1,2})\s*\+\s*years?\b', re.IGNORECASE),
    re.compile(r'(?:at least|minimum|min\.?|over)\s*(\d{1,2})\s*years?\b', re.IGNORECASE),
    re.compile(r'(\d{1,2})\s*years?\s*(?:of\s+)?(?:experience|exp\.?)\b', re.IGNORECASE),
]

# Map years → seniority
def _years_to_seniority(years_min: int) -> str:
    if years_min <= 0:
        return "intern"
    elif years_min <= 1:
        return "entry"
    elif years_min <= 3:
        return "mid"
    elif years_min <= 6:
        return "senior"
    elif years_min <= 10:
        return "lead"
    else:
        return "director"


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
    title_lower = title.lower()
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
    """Extract min/max years of experience from description text."""
    all_mins = []
    all_maxs = []

    for pattern in _YEARS_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groups()
            if len(groups) == 2 and groups[1]:
                # Range: "3-5 years"
                try:
                    mn, mx = int(groups[0]), int(groups[1])
                    if mn <= 30 and mx <= 30:  # sanity check
                        all_mins.append(mn)
                        all_maxs.append(mx)
                except ValueError:
                    pass
            elif len(groups) >= 1:
                # Single: "5+ years"
                try:
                    val = int(groups[0])
                    if val <= 30:
                        all_mins.append(val)
                except ValueError:
                    pass

    if not all_mins:
        return None, None

    # Take the most commonly requested minimum
    years_min = min(all_mins)
    years_max = max(all_maxs) if all_maxs else None
    return years_min, years_max
