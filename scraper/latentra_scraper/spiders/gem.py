"""
Gem ATS spider. Gem's public job board API mirrors Greenhouse's shape:
    GET https://api.gem.com/job_board/v0/{slug}/job_posts/
        → [{id, title, location{name}, absolute_url, first_published_at,
            updated_at, content (HTML), departments[], employment_type}]
"""

from latentra_scraper.items import JobItem
from latentra_scraper.spiders.base_job_spider import BaseJobSpider


class GemSpider(BaseJobSpider):
    name = "gem"
    source_type = "gem"
    allowed_domains = ["api.gem.com"]

    def build_url(self, source):
        return source.get("base_url") or f"https://api.gem.com/job_board/v0/{source['company_slug']}/job_posts/"

    def parse_jobs(self, response, source):
        data = self.safe_json_parse(response.text)
        if isinstance(data, dict):  # tolerate {"job_posts": [...]} / {"jobs": [...]}
            data = data.get("job_posts") or data.get("jobs") or []
        slug = source["company_slug"]
        if not isinstance(data, list):
            self.logger.error(f"[{slug}] Invalid JSON response")
            return
        self.logger.info(f"[{slug}] Found {len(data)} jobs")

        for job in data:
            job_id = job.get("id")
            if not job_id:
                continue
            loc = job.get("location")
            location = loc.get("name", "") if isinstance(loc, dict) else (loc or "")
            depts = job.get("departments") or []
            department = depts[0].get("name") if depts and isinstance(depts[0], dict) else None
            emp = (job.get("employment_type") or "").lower()
            employment_type = (
                "full_time" if "full" in emp else
                "part_time" if "part" in emp else
                "intern" if "intern" in emp else
                "contract" if "contract" in emp else "unknown"
            )
            yield JobItem(
                source="gem",
                source_job_id=str(job_id),
                title=self.safe_text(job.get("title")),
                company=self.company_display_name(source),
                company_slug=slug,
                department=department,
                seniority="unknown",
                location=location,
                remote_type="remote" if "remote" in location.lower() else "unknown",
                employment_type=employment_type,
                salary_min=None,
                salary_max=None,
                salary_currency=None,
                description_html=job.get("content") or job.get("content_plain") or "",
                tags=None,
                apply_url=job.get("absolute_url", ""),
                posted_at=self.safe_datetime_iso(job.get("first_published_at") or job.get("updated_at")),
            )
