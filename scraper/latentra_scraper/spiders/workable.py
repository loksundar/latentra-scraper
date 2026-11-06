"""
Workable ATS spider.

One request per company — the public careers widget returns every published
job with its full description:
    GET https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true
"""

from latentra_scraper.items import JobItem
from latentra_scraper.spiders.base_job_spider import BaseJobSpider


class WorkableSpider(BaseJobSpider):
    name = "workable"
    source_type = "workable"
    allowed_domains = ["apply.workable.com"]

    def build_url(self, source):
        return source.get("base_url") or (
            f"https://apply.workable.com/api/v1/widget/accounts/{source['company_slug']}?details=true"
        )

    def parse_jobs(self, response, source):
        data = self.safe_json_parse(response.text)
        if not isinstance(data, dict):
            self.logger.error(f"[{source['company_slug']}] Invalid JSON response")
            return

        jobs = data.get("jobs") or []
        slug = source["company_slug"]
        company = source.get("company_name") or data.get("name") or self.company_display_name(source)
        self.logger.info(f"[{slug}] Found {len(jobs)} jobs")

        for job in jobs:
            shortcode = job.get("shortcode")
            if not shortcode:
                continue

            parts = [job.get("city"), job.get("state"), job.get("country")]
            location = ", ".join(p for p in parts if p)
            if job.get("telecommuting"):
                remote_type = "remote"
                location = location or "Remote"
            else:
                remote_type = "unknown"

            emp = (job.get("employment_type") or "").lower()
            if "full" in emp:
                employment_type = "full_time"
            elif "part" in emp:
                employment_type = "part_time"
            elif "contract" in emp or "temporary" in emp:
                employment_type = "contract"
            elif "intern" in emp:
                employment_type = "intern"
            else:
                employment_type = "unknown"

            published = job.get("published_on") or job.get("created_at")
            posted_at = f"{published} 00:00:00" if published and len(published) == 10 else self.safe_datetime_iso(published)

            yield JobItem(
                source="workable",
                source_job_id=str(shortcode),
                title=self.safe_text(job.get("title")),
                company=company,
                company_slug=slug,
                department=job.get("department") or None,
                seniority="unknown",
                location=location,
                remote_type=remote_type,
                employment_type=employment_type,
                salary_min=None,
                salary_max=None,
                salary_currency=None,
                description_html=job.get("description", ""),
                tags=None,
                apply_url=job.get("url") or f"https://apply.workable.com/{slug}/j/{shortcode}/",
                posted_at=posted_at,
            )
