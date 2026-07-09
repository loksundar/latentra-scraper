"""
Enrichment pipeline — runs after normalization.
Calls all enrichment modules: skills, location, platform keywords, seniority.
"""

from latentra_scraper.enrichment.skills import extract_skills
from latentra_scraper.enrichment.location import normalize_location
from latentra_scraper.enrichment.platform_keywords import extract_platform_keywords
from latentra_scraper.enrichment.seniority import infer_seniority
from latentra_scraper.enrichment.quality import compute_quality


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

        # 5. Remote type upgrade — use location + description if still unknown
        if item.get("remote_type") in (None, "unknown"):
            if loc["is_remote_friendly"]:
                item["remote_type"] = "remote"

        # 6. Quality scoring
        q = compute_quality(dict(item))
        item["quality_score"] = q["quality_score"]
        item["quality_flags"] = q["quality_flags"]
        item["description_word_count"] = q["description_word_count"]

        return item
