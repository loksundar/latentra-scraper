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
            wrapped = data.get("job_posts") if "job_posts" in data else data.get("jobs")
            # {"code": 404, "message": "Job board ... not found"} and friends
            # are errors, not an empty board.
            data = wrapped if isinstance(wrapped, list) else None
        slug = source["company_slug"]
        if not isinstance(data, list):
            self.mark_source_failed(response, "Invalid JSON response (not a job_posts list)")
            return
        self.logger.info(f"[{slug}] Found {len(data)} jobs")

        for job in data:
            if not isinstance(job, dict):
                continue
            job_id = job.get("id")
            if not job_id:
                continue
            loc = job.get("location")
            location = self.safe_text(loc.get("name")) if isinstance(loc, dict) else self.safe_text(loc)
            depts = job.get("departments")
            department = None
            if isinstance(depts, list) and depts and isinstance(depts[0], dict):
                department = depts[0].get("name")
            emp = self.safe_text(job.get("employment_type")).lower()
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
                description_html=next((v for v in (job.get("content"), job.get("content_plain"))
                                       if isinstance(v, str) and v), ""),
                tags=None,
                apply_url=job.get("absolute_url") if isinstance(job.get("absolute_url"), str) else "",
                posted_at=self.safe_datetime_iso(job.get("first_published_at") or job.get("updated_at")),
            )
