"""
Workday ATS spider.

Workday uses a POST-based JSON API at:
    POST /wday/cxs/{tenant}/{site_id}/jobs     — paginated listing (max 20/page)
    GET  /wday/cxs/{tenant}/{site_id}/job/{slug} — full job detail

Each source in job_sources must have:
    base_url = "https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site_id}"
    (or custom domain variant like "https://explore.jobs.netflix.net/wday/cxs/netflix/netflix-careers")
"""

import json

import scrapy

from latentra_scraper.items import JobItem
from latentra_scraper.spiders.base_job_spider import BaseJobSpider


class WorkdaySpider(BaseJobSpider):
    name = "workday"
    source_type = "workday"

    PAGE_SIZE = 20

    def build_url(self, source):
        return source.get("base_url", "")

    def build_requests(self, source):
        """Override: Workday uses POST requests."""
        base_url = source.get("base_url")
        if not base_url:
            self.logger.error(f"[{source['company_slug']}] Missing base_url for Workday source")
            return

        jobs_url = base_url.rstrip("/") + "/jobs"
        yield scrapy.Request(
            jobs_url,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            body=json.dumps({"appliedFacets": {}, "limit": self.PAGE_SIZE, "offset": 0, "searchText": ""}),
            callback=self.parse_listing,
            cb_kwargs={"source": source, "offset": 0},
            errback=self.handle_error,
        )

    def parse_listing(self, response, source, offset):
        data = self.safe_json_parse(response.text)
        if not data:
            self.logger.error(f"[{source['company_slug']}] Invalid JSON from listing")
            return

        total = data.get("total", 0)
        postings = data.get("jobPostings", [])
        slug = source["company_slug"]

        if offset == 0:
            self.logger.info(f"[{slug}] Total jobs: {total}")

        base_url = source["base_url"].rstrip("/")

        # Fetch detail page for each posting
        for posting in postings:
            ext_path = posting.get("externalPath", "")
            if not ext_path:
                continue

            # externalPath looks like "/en-US/SiteId/job/Location/Title_JRID"
            # We need just the part after the site_id for the detail URL
            # The detail endpoint is: base_url + "/job/" + last_segment
            # But safer: just use base_url's domain + externalPath
            detail_url = self._build_detail_url(base_url, ext_path)

            yield scrapy.Request(
                detail_url,
                headers={"Accept": "application/json"},
                callback=self.parse_detail,
                cb_kwargs={
                    "source": source,
                    "listing": posting,
                },
                errback=self.handle_error,
            )

        # Paginate
        next_offset = offset + self.PAGE_SIZE
        if next_offset < total:
            jobs_url = base_url + "/jobs"
            yield scrapy.Request(
                jobs_url,
                method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                body=json.dumps({"appliedFacets": {}, "limit": self.PAGE_SIZE, "offset": next_offset, "searchText": ""}),
                callback=self.parse_listing,
                cb_kwargs={"source": source, "offset": next_offset},
                errback=self.handle_error,
            )

    def parse_detail(self, response, source, listing):
        raw = self.safe_json_parse(response.text)
        if not raw:
            self.logger.warning(f"[{source['company_slug']}] No detail for {listing.get('title', '?')}")
            return

        # Workday nests job data under jobPostingInfo
        data = raw.get("jobPostingInfo") or raw

        company_name = self.company_display_name(source)
        company_slug = source["company_slug"]

        # Extract job req ID as source_job_id
        job_id = data.get("jobReqId") or ""
        if not job_id:
            bullets = listing.get("bulletFields") or []
            job_id = bullets[0] if bullets else listing.get("externalPath", "").rsplit("/", 1)[-1]

        # Employment type from timeType
        time_type = (data.get("timeType") or "").lower()
        if "full" in time_type:
            employment_type = "full_time"
        elif "part" in time_type:
            employment_type = "part_time"
        else:
            employment_type = "unknown"

        # Department from jobFamilyGroup (may be at root or in jobPostingInfo)
        family_groups = raw.get("jobFamilyGroup") or data.get("jobFamilyGroup") or []
        department = family_groups[0] if family_groups else None

        # Posted date — may be ISO or relative ("Posted 3 Days Ago")
        posted_on = data.get("postedOn", "")
        posted_at = self.safe_datetime_iso(posted_on) if posted_on and not posted_on.startswith("Posted") else None

        # Apply URL — human-readable careers page
        # Workday's externalPath is usually just "/job/{Location}/{Title}_{JRID}" and
        # requires the "/en-US/{site_id}" prefix to resolve. Some tenants already
        # include "/en-US/{site_id}" in externalPath — detect and pass through.
        ext_path = listing.get("externalPath", "")
        base_domain = source["base_url"].split("/wday/")[0]
        site_id = source["base_url"].rstrip("/").rsplit("/", 1)[-1]
        if not ext_path:
            apply_url = ""
        elif ext_path.startswith("/en-US/") or ext_path.startswith(f"/{site_id}/"):
            apply_url = base_domain + ext_path
        else:
            apply_url = f"{base_domain}/en-US/{site_id}{ext_path}"

        yield JobItem(
            source="workday",
            source_job_id=str(job_id),
            title=self.safe_text(data.get("title") or listing.get("title")),
            company=company_name,
            company_slug=company_slug,
            department=department,
            seniority="unknown",
            location=self.safe_text(data.get("location") or listing.get("locationsText")),
            remote_type="unknown",
            employment_type=employment_type,
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            description_html=data.get("jobDescription", ""),
            tags=None,
            apply_url=apply_url,
            posted_at=posted_at,
        )

    def _build_detail_url(self, base_url: str, ext_path: str) -> str:
        """
        Convert externalPath to a detail API URL.
        externalPath: /en-US/SiteId/job/Location/Title_JRID
        detail URL:   base_url/job/Location/Title_JRID
        """
        # Find '/job/' in the path and take everything from there
        job_idx = ext_path.find("/job/")
        if job_idx != -1:
            return base_url.rstrip("/") + ext_path[job_idx:]
        # Fallback: append the whole path
        return base_url.rstrip("/") + ext_path
