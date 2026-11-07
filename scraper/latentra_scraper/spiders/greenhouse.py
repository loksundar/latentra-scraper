from latentra_scraper.items import JobItem
from latentra_scraper.spiders.base_job_spider import BaseJobSpider


class GreenhouseSpider(BaseJobSpider):
    name = "greenhouse"
    source_type = "greenhouse"
    allowed_domains = ["boards-api.greenhouse.io"]

    def build_url(self, source):
        slug = source["company_slug"]
        base = source.get("base_url")
        if base:
            return base
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

    def parse_jobs(self, response, source):
        data = self.safe_json_parse(response.text)
        # A board always answers {"jobs": [...], "meta": {...}}; anything else
        # (error object, HTML interstitial) is a failed check, not an empty board.
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            self.mark_source_failed(response, "Invalid JSON response (no jobs list)")
            return

        jobs = data["jobs"]
        company_slug = source["company_slug"]
        company_name = self.company_display_name(source)
        self.logger.info(f"[{company_slug}] Found {len(jobs)} jobs")

        for job in jobs:
            if not isinstance(job, dict):
                continue
            # Location
            location = ""
            loc_data = job.get("location")
            if isinstance(loc_data, dict):
                location = self.safe_text(loc_data.get("name"))
            elif isinstance(loc_data, str):
                location = loc_data

            # Department
            department = None
            depts = job.get("departments")
            if isinstance(depts, list):
                dept_names = [d.get("name") for d in depts if isinstance(d, dict) and d.get("name")]
                if dept_names:
                    department = dept_names[0]

            yield JobItem(
                source="greenhouse",
                source_job_id=str(job.get("id", "")),
                title=self.safe_text(job.get("title")),
                company=company_name,
                company_slug=company_slug,
                department=department,
                seniority="unknown",
                location=location,
                remote_type="unknown",
                employment_type="unknown",
                salary_min=None,
                salary_max=None,
                salary_currency=None,
                description_html=job.get("content") if isinstance(job.get("content"), str) else "",
                tags=None,
                apply_url=job.get("absolute_url") if isinstance(job.get("absolute_url"), str) else "",
                posted_at=self.safe_datetime_iso(job.get("updated_at")),
            )
