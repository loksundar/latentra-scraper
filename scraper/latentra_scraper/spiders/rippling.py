"""
Rippling ATS spider.

    GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs
        → [{uuid, name, department{label}, url, workLocation{label}}]   (all jobs, no paging)
    GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uuid}
        → full posting: description{company, role} HTML, workLocations,
          employmentType, createdOn, payRangeDetails, companyName

The listing has no description or date, so details are fetched — but only
for rows that pass the shared title/US prefilter.
"""

import scrapy

from latentra_scraper.items import JobItem
from latentra_scraper.spiders.base_job_spider import BaseJobSpider

API = "https://api.rippling.com/platform/api/ats/v1/board"


class RipplingSpider(BaseJobSpider):
    name = "rippling"
    source_type = "rippling"
    allowed_domains = ["api.rippling.com"]

    def build_url(self, source):
        return f"{API}/{source['company_slug']}/jobs"

    def parse_jobs(self, response, source):
        jobs = self.safe_json_parse(response.text)
        slug = source["company_slug"]
        if not isinstance(jobs, list):
            self.logger.error(f"[{slug}] Invalid JSON response")
            return
        self.logger.info(f"[{slug}] Found {len(jobs)} jobs")

        for job in jobs:
            uuid = job.get("uuid")
            if not uuid:
                continue
            location = (job.get("workLocation") or {}).get("label", "")
            if self.skip_from_listing(job.get("name"), location):
                self.count_prefiltered()
                continue
            yield scrapy.Request(
                f"{API}/{slug}/jobs/{uuid}",
                callback=self.parse_detail,
                cb_kwargs={"source": source, "listing": job},
                errback=self.handle_error,
            )

    def parse_detail(self, response, source, listing):
        d = self.safe_json_parse(response.text)
        if not isinstance(d, dict):
            self.logger.warning(f"[{source['company_slug']}] No detail for {listing.get('name', '?')}")
            return

        desc = d.get("description") or {}
        if isinstance(desc, dict):
            description_html = (desc.get("role") or "") + (desc.get("company") or "")
        else:
            description_html = str(desc)

        locations = d.get("workLocations") or []
        location = "; ".join(l for l in locations if isinstance(l, str)) or \
            (listing.get("workLocation") or {}).get("label", "")
        remote_type = "remote" if "remote" in location.lower() else "unknown"

        emp_label = ((d.get("employmentType") or {}).get("label") or "").upper()
        if emp_label.endswith("_FT") or "FULL" in emp_label:
            employment_type = "full_time"
        elif emp_label.endswith("_PT") or "PART" in emp_label:
            employment_type = "part_time"
        elif "INTERN" in emp_label:
            employment_type = "intern"
        elif "CONTRACT" in emp_label or "TEMP" in emp_label:
            employment_type = "contract"
        else:
            employment_type = "unknown"

        salary_min = salary_max = salary_currency = None
        for pay in d.get("payRangeDetails") or []:
            if (pay.get("frequency") or "").upper() == "YEAR" and pay.get("rangeStart"):
                salary_min = int(pay["rangeStart"])
                salary_max = int(pay["rangeEnd"]) if pay.get("rangeEnd") else None
                salary_currency = pay.get("currency") or "USD"
                break

        dept = d.get("department") or {}
        department = dept.get("name") if isinstance(dept, dict) else None

        yield JobItem(
            source="rippling",
            source_job_id=str(d.get("uuid") or listing["uuid"]),
            title=self.safe_text(d.get("name") or listing.get("name")),
            company=source.get("company_name") or d.get("companyName") or self.company_display_name(source),
            company_slug=source["company_slug"],
            department=department or (listing.get("department") or {}).get("label"),
            seniority="unknown",
            location=location,
            remote_type=remote_type,
            employment_type=employment_type,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            description_html=description_html,
            tags=None,
            apply_url=d.get("url") or listing.get("url", ""),
            posted_at=self.safe_datetime_iso(d.get("createdOn")),
        )
