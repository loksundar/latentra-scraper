"""
Enrichment pipeline — runs after normalization.
Calls all enrichment modules: skills, location, platform keywords, seniority.
"""

import re

from latentra_scraper.enrichment.skills import extract_skills
from latentra_scraper.enrichment.location import normalize_location
from latentra_scraper.enrichment.platform_keywords import extract_platform_keywords
from latentra_scraper.enrichment.seniority import infer_seniority
from latentra_scraper.enrichment.quality import compute_quality
from latentra_scraper.enrichment.salary import extract_salary
from latentra_scraper.schema import conform_item

_HYBRID_IN_LOCATION = re.compile(r"\bhybrid\b", re.I)
_INTERN_TITLE = re.compile(r"\b(intern|internship|co-op|coop)\b", re.I)


class EnrichPipeline:
    """Run all enrichment modules on each job item."""

    def process_item(self, item, spider):
        title = item.get("title", "")
        desc = item.get("description_text", "")

        # 1. Skills (title + description)
        item["skills"] = extract_skills(title, desc)

        # 2. Location normalization
        loc = normalize_location(
            item.get("location", ""),
            title=title,
            description_text=desc,
        )
        item["location_city"] = loc["location_city"]
        item["location_state"] = loc["location_state"]
        item["location_country"] = loc["location_country"]
        item["is_remote_friendly"] = loc["is_remote_friendly"]

        # 3. Platform keywords
        plat = extract_platform_keywords(desc)
        item["platform_keywords"] = plat["platform_keywords"]
        item["primary_cloud_platforms"] = plat["primary_cloud_platforms"]
        item["is_multi_cloud"] = plat["is_multi_cloud"]
        item["is_platform_heavy"] = plat["is_platform_heavy"]

        # 4. Seniority (upgraded — uses title + years from description)
        sen = infer_seniority(title, desc)
        item["seniority"] = sen["seniority"]
        item["seniority_confidence"] = sen["seniority_confidence"]
        item["years_experience_min"] = sen["years_experience_min"]
        item["years_experience_max"] = sen["years_experience_max"]

        # 5. Remote type upgrade when the ATS gave none. Only the location or
        #    the title may flip it to "remote": the bare word in a description
        #    ("distributed systems", "remote teams") made 41% of a 6.7k-job
        #    sample "remote" while sitting in Seattle. A description that
        #    states the role itself is remote ("fully remote") still counts,
        #    and a location that says Hybrid is hybrid.
        if item.get("remote_type") in (None, "unknown"):
            location = item.get("location") or ""
            if _HYBRID_IN_LOCATION.search(location):
                item["remote_type"] = "hybrid"
            elif loc["is_remote_friendly"]:
                item["remote_type"] = "remote"

        # 5b. Internships: ATSs file them as "Full-time" (Lever commitment,
        #     Amazon, Workday) — the title is the better source. Both ways.
        if sen["seniority"] == "intern" and sen["seniority_confidence"] == "title" \
                and item.get("employment_type") in (None, "unknown", "full_time", "part_time") \
                and _INTERN_TITLE.search(title or ""):
            item["employment_type"] = "intern"
        elif item.get("employment_type") == "intern" and item.get("seniority") != "intern":
            item["seniority"] = "intern"
            item["seniority_confidence"] = "employment"

        # 6. Salary from the description when the ATS didn't supply one.
        #    Never allowed to break the item: any error just leaves salary empty.
        if not item.get("salary_min") and not item.get("salary_max"):
            try:
                found = extract_salary(desc)
                if found:
                    item["salary_min"], item["salary_max"] = found
                    item["salary_currency"] = "USD"
            except Exception:
                pass

        # 7. Quality scoring
        q = compute_quality(dict(item))
        item["quality_score"] = q["quality_score"]
        item["quality_flags"] = q["quality_flags"]
        item["description_word_count"] = q["description_word_count"]

        # 8. Fit the DB contract (column lengths, enums, integer ranges, 0/1
        #    booleans) so one oversize value cannot sink the company batch.
        notes = conform_item(item)
        if notes and spider is not None:
            spider.logger.warning(
                f"[{item.get('company_slug')}] {item.get('source_job_id')}: " + "; ".join(notes)
            )

        return item
