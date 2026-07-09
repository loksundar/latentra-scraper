"""
Quality scoring — heuristic quality score for job postings.

Flags potential ghost listings, thin descriptions, and stale posts.
Score starts at 100, deductions applied for each flag.
"""

import json
from datetime import datetime, timedelta, timezone


def compute_quality(item: dict) -> dict:
    """
    Compute quality score and flags for a job item.

    Returns:
        {
            "quality_score": int (0-100),
            "quality_flags": list[str],
            "description_word_count": int,
        }
    """
    score = 100
    flags = []

    # ── Description length ──────────────────────────
    desc_text = item.get("description_text") or ""
    word_count = len(desc_text.split()) if desc_text.strip() else 0

    if word_count < 50:
        flags.append("short_description")
        score -= 20
    elif word_count < 100:
        flags.append("brief_description")
        score -= 10

    # ── Posting age ─────────────────────────────────
    posted_at = item.get("posted_at")
    if posted_at:
        try:
            if isinstance(posted_at, str):
                # Handle ISO format strings
                posted_dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
            else:
                posted_dt = posted_at

            if posted_dt.tzinfo is None:
                posted_dt = posted_dt.replace(tzinfo=timezone.utc)

            age_days = (datetime.now(timezone.utc) - posted_dt).days
            if age_days > 60:
                flags.append("old_posting")
                score -= 15
            elif age_days > 30:
                flags.append("aging_posting")
                score -= 10
        except (ValueError, TypeError):
            pass

    # ── Missing salary ──────────────────────────────
    if not item.get("salary_min") and not item.get("salary_max"):
        score -= 10

    # ── Missing metadata ────────────────────────────
    seniority = item.get("seniority") or "unknown"
    employment = item.get("employment_type") or "unknown"
    if seniority == "unknown" and employment == "unknown":
        flags.append("missing_metadata")
        score -= 10

    if not item.get("department"):
        score -= 5

    # Clamp to 0-100
    score = max(0, min(100, score))

    return {
        "quality_score": score,
        "quality_flags": flags,
        "description_word_count": word_count,
    }
