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
        if not data:
            self.logger.error(f"[{source['company_slug']}] Invalid JSON response")
            return

        jobs = data.get("jobs", [])
        company_slug = source["company_slug"]
        company_name = self.company_display_name(source)
        self.logger.info(f"[{company_slug}] Found {len(jobs)} jobs")

        for job in jobs:
            # Location
            location = ""
            loc_data = job.get("location")
            if isinstance(loc_data, dict):
                location = loc_data.get("name", "")
            elif isinstance(loc_data, str):
                location = loc_data

            # Department
            department = None
            depts = job.get("departments", [])
            if depts and isinstance(depts, list):
                dept_names = [d.get("name", "") for d in depts if d.get("name")]
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
                description_html=job.get("content", ""),
                tags=None,
                apply_url=job.get("absolute_url", ""),
                posted_at=self.safe_datetime_iso(job.get("updated_at")),
            )
