"""
Amazon careers spider (single company, public search JSON).

    GET https://www.amazon.jobs/en/search.json
        ?normalized_country_code[]=USA&category[]={cat}&result_limit=100&offset={n}&sort=recent

Results already include description + qualifications, so no detail fetches.
Only tech job categories are crawled (~4.7k US jobs as of 2026-11).
"""

import re
from datetime import datetime
from urllib.parse import urlencode

import scrapy

from latentra_scraper.items import JobItem
from latentra_scraper.spiders.base_job_spider import BaseJobSpider

SEARCH = "https://www.amazon.jobs/en/search.json"

TECH_CATEGORIES = [
    "software-development",
    "machine-learning-science",
    "data-science",
    "research-science",
    "solutions-architect",
    "systems-quality-security-engineering",
    "hardware-development",
    "business-intelligence",
    "database-administration",
    "project-program-product-management-technical",
]


class AmazonSpider(BaseJobSpider):
    name = "amazon"
    source_type = "amazon"
    allowed_domains = ["www.amazon.jobs"]

    PAGE_SIZE = 100
    MAX_OFFSET = 3000  # per category; API caps deep paging anyway

    def _url(self, category, offset):
        return SEARCH + "?" + urlencode({
            "normalized_country_code[]": "USA",
            "category[]": category,
            "result_limit": self.PAGE_SIZE,
            "offset": offset,
            "sort": "recent",
        })

    def build_requests(self, source):
        for cat in TECH_CATEGORIES:
            yield scrapy.Request(
                self._url(cat, 0),
                headers={"Accept": "application/json"},
                callback=self.parse_page,
                cb_kwargs={"source": source, "category": cat, "offset": 0},
                errback=self.handle_error,
            )

    def parse_page(self, response, source, category, offset):
        data = self.safe_json_parse(response.text)
        # A good page is {"error": null, "hits": N, "jobs": [...]}. An error
        # string or a missing jobs list is a failed category, not an empty one.
        if not isinstance(data, dict) or data.get("error") or not isinstance(data.get("jobs"), list):
            self.mark_source_failed(response, f"[amazon/{category}] Invalid search response")
            return
        jobs = [j for j in data["jobs"] if isinstance(j, dict)]
        hits = data.get("hits") if isinstance(data.get("hits"), int) else 0
        if offset == 0:
            self.logger.info(f"[amazon/{category}] Total jobs: {hits}")

        yield from self.parse_jobs_list(jobs, source)

        nxt = offset + self.PAGE_SIZE
        if jobs and nxt < hits and nxt < self.MAX_OFFSET:
            yield scrapy.Request(
                self._url(category, nxt),
                headers={"Accept": "application/json"},
                callback=self.parse_page,
                cb_kwargs={"source": source, "category": category, "offset": nxt},
                errback=self.handle_error,
            )

    def parse_jobs_list(self, jobs, source):
        for job in jobs:
            job_id = job.get("id_icims") or job.get("id")
            if not job_id:
                continue
            desc = job.get("description") or ""
            if job.get("basic_qualifications"):
                desc += "<h3>Basic Qualifications</h3>" + job["basic_qualifications"]
            if job.get("preferred_qualifications"):
                desc += "<h3>Preferred Qualifications</h3>" + job["preferred_qualifications"]

            sched = (job.get("job_schedule_type") or "").lower()
            employment_type = ("intern" if job.get("is_intern") else
                               "full_time" if "full" in sched else
                               "part_time" if "part" in sched else "unknown")

            yield JobItem(
                source="amazon",
                source_job_id=str(job_id),
                title=self.safe_text(job.get("title")),
                company=source.get("company_name") or "Amazon",  # job.company_name is the legal entity
                company_slug=source["company_slug"],
                department=job.get("job_category") or job.get("business_category"),
                seniority="unknown",
                location=job.get("normalized_location") or job.get("location") or "",
                remote_type="unknown",
                employment_type=employment_type,
                salary_min=None,
                salary_max=None,
                salary_currency=None,
                description_html=desc,
                tags=None,
                apply_url="https://www.amazon.jobs" + (job.get("job_path") or f"/en/jobs/{job_id}"),
                posted_at=parse_amazon_date(job.get("posted_date")),
            )


def parse_amazon_date(value):
    """'November  6, 2026' -> '2026-11-06 00:00:00'."""
    if not value:
        return None
    try:
        return datetime.strptime(re.sub(r"\s+", " ", value.strip()), "%B %d, %Y").strftime("%Y-%m-%d 00:00:00")
    except ValueError:
        return None
