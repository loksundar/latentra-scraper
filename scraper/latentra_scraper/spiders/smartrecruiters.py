"""
SmartRecruiters ATS spider.

SmartRecruiters provides a public API at:
    GET https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0

Pagination: offset-based. Increment offset by limit until fewer results than limit.
Each posting includes: id, name, location, department, typeOfEmployment,
experienceLevel, jobAd (with description sections), releasedDate.
"""

import json
import scrapy
from latentra_scraper.items import JobItem
from latentra_scraper.sources.loader import get_active_sources
from latentra_scraper.spiders.base_job_spider import BaseJobSpider


class SmartRecruitersSpider(BaseJobSpider):
    name = "smartrecruiters"
    source_type = "smartrecruiters"
    allowed_domains = ["api.smartrecruiters.com"]

    PAGE_SIZE = 100

    def build_url(self, source):
        slug = source["company_slug"]
        base = source.get("base_url")
        if base:
            return base
        return f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit={self.PAGE_SIZE}&offset=0"

    def start_requests(self):
        """Override to handle pagination — build initial URLs for each source."""
        sources = get_active_sources(self.source_type)
        if not sources:
            self.logger.warning(f"No active sources for {self.source_type}")
            return

        for source in sources:
            url = self.build_url(source)
            yield scrapy.Request(
                url,
                callback=self.parse,
                cb_kwargs={"source": source, "offset": 0},
                dont_filter=True,
            )

    def parse(self, response, source, offset=0):
        """Handle pagination and delegate to parse_jobs."""
        import scrapy

        data = self.safe_json_parse(response.text)
        if not data:
            self.logger.error(f"[{source['company_slug']}] Invalid JSON response")
            return

        content = data.get("content", [])
        company_slug = source["company_slug"]
        total = data.get("totalFound", len(content))
        self.logger.info(f"[{company_slug}] Page offset={offset}, got {len(content)} postings (total: {total})")

        for item in self._parse_postings(content, source):
            yield item

        # Paginate if there are more results
        if len(content) >= self.PAGE_SIZE:
            next_offset = offset + self.PAGE_SIZE
            slug = source["company_slug"]
            next_url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit={self.PAGE_SIZE}&offset={next_offset}"
            yield scrapy.Request(
                next_url,
                callback=self.parse,
                cb_kwargs={"source": source, "offset": next_offset},
                dont_filter=True,
            )

    def parse_jobs(self, response, source):
        """Called by base class — but we override parse() for pagination."""
        data = self.safe_json_parse(response.text)
        if not data:
            return
        content = data.get("content", [])
        yield from self._parse_postings(content, source)

    def _parse_postings(self, postings, source):
        """Parse a list of SmartRecruiters posting objects into JobItems."""
        company_slug = source["company_slug"]
        company_name = self.company_display_name(source)

        for posting in postings:
            posting_id = posting.get("id", "")
            if not posting_id:
                continue

            # Location
            loc = posting.get("location", {})
            city = loc.get("city", "")
            country = loc.get("country", "")
            region = loc.get("region", "")
            location_parts = [p for p in [city, region, country] if p]
            location_str = ", ".join(location_parts) if location_parts else None

            # Remote type
            remote_status = (loc.get("remote") or False)
            if remote_status is True:
                remote_type = "remote"
            else:
                remote_type = "unknown"

            # Employment type
            emp_raw = (posting.get("typeOfEmployment") or "").lower()
            if "full" in emp_raw:
                employment_type = "full_time"
            elif "part" in emp_raw:
                employment_type = "part_time"
            elif "contract" in emp_raw or "freelance" in emp_raw:
                employment_type = "contract"
            elif "intern" in emp_raw:
                employment_type = "intern"
            else:
                employment_type = "unknown"

            # Description — combine sections from jobAd
            description_html = ""
            job_ad = posting.get("jobAd", {})
            sections = job_ad.get("sections", {})
            for section_key in ["jobDescription", "qualifications", "additionalInformation", "companyDescription"]:
                section = sections.get(section_key, {})
                text = section.get("text", "")
                if text:
                    title = section.get("title", "")
                    if title:
                        description_html += f"<h3>{title}</h3>"
                    description_html += text

            # Department
            dept = posting.get("department", {})
            department = dept.get("label") if isinstance(dept, dict) else None

            # Apply URL
            apply_url = f"https://jobs.smartrecruiters.com/{company_slug}/{posting_id}"
            ref = posting.get("ref")
            if ref:
                apply_url = ref

            # Posted date
            released = posting.get("releasedDate", "")

            yield JobItem(
                source="smartrecruiters",
                source_job_id=str(posting_id),
                title=self.safe_text(posting.get("name")),
                company=company_name,
                company_slug=company_slug,
                department=department,
                seniority="unknown",
                location=location_str,
                remote_type=remote_type,
                employment_type=employment_type,
                salary_min=None,
                salary_max=None,
                salary_currency=None,
                description_html=description_html,
                tags=None,
                apply_url=apply_url,
                posted_at=self.safe_datetime_iso(released) if released else None,
            )
