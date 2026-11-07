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

    def _skip_from_listing(self, posting) -> bool:
        return self.skip_from_listing(posting.get("title"), posting.get("locationsText"))

    @staticmethod
    def _country(data: dict) -> str:
        """ISO code or name of the requisition's primary country, '' if absent.
        jobRequisitionLocation.country.alpha2Code ("US") is preferred; the
        older `country.descriptor` ("United States of America") is the fallback."""
        jrl = data.get("jobRequisitionLocation")
        c = jrl.get("country") if isinstance(jrl, dict) else None
        if isinstance(c, dict):
            for key in ("alpha2Code", "descriptor"):
                if isinstance(c.get(key), str) and c[key].strip():
                    return c[key]
        c = data.get("country")
        if isinstance(c, dict) and isinstance(c.get("descriptor"), str):
            return c["descriptor"]
        return ""

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
        # A real listing always carries an integer "total" (0 for an empty
        # site). An error object / {} / HTML interstitial must not be mistaken
        # for "0 jobs" — that would heartbeat the tenant as healthy.
        if not isinstance(data, dict) or not isinstance(data.get("total"), int):
            self.mark_source_failed(response, "Invalid JSON from listing (no total)")
            return

        total = data["total"]
        postings = data.get("jobPostings")
        if not isinstance(postings, list):
            postings = []
        slug = source["company_slug"]

        if offset == 0:
            self.logger.info(f"[{slug}] Total jobs: {total}")

        base_url = source["base_url"].rstrip("/")

        # Fetch detail page for each posting
        for posting in postings:
            if not isinstance(posting, dict):
                continue
            ext_path = posting.get("externalPath")
            if not isinstance(ext_path, str) or not ext_path:
                continue
            if self._skip_from_listing(posting):
                self.count_prefiltered()
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

        # Paginate — only while pages still carry rows, so a tenant whose
        # "total" overstates what it will actually serve can't keep us paging.
        next_offset = offset + self.PAGE_SIZE
        if postings and next_offset < total:
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
        listing = listing if isinstance(listing, dict) else {}
        if not isinstance(raw, dict) or not raw:
            self.logger.warning(f"[{source['company_slug']}] No detail for {listing.get('title', '?')}")
            return

        # Workday nests job data under jobPostingInfo
        data = raw.get("jobPostingInfo")
        if not isinstance(data, dict):
            data = raw

        # Country of the requisition's primary location — authoritative for
        # the US filter ("2 Locations" / a bare city tells the validator nothing).
        country = self._country(data)
        if self.is_non_us_country(country):
            self.count_prefiltered()
            return

        company_name = self.company_display_name(source)
        company_slug = source["company_slug"]

        # Extract job req ID as source_job_id
        job_id = self.safe_text(data.get("jobReqId"))
        if not job_id:
            bullets = listing.get("bulletFields")
            if isinstance(bullets, list) and bullets and isinstance(bullets[0], str):
                job_id = bullets[0]
            else:
                job_id = self.safe_text(listing.get("externalPath")).rsplit("/", 1)[-1]

        # Employment type from timeType
        time_type = self.safe_text(data.get("timeType")).lower()
        if "full" in time_type:
            employment_type = "full_time"
        elif "part" in time_type:
            employment_type = "part_time"
        else:
            employment_type = "unknown"

        # Department from jobFamilyGroup (may be at root or in jobPostingInfo)
        family_groups = raw.get("jobFamilyGroup") or data.get("jobFamilyGroup")
        department = None
        if isinstance(family_groups, list) and family_groups:
            first = family_groups[0]
            if isinstance(first, dict):
                first = first.get("descriptor")
            department = self.safe_text(first) or None

        # Posted date — postedOn is relative ("Posted Yesterday") on nearly every
        # tenant; the real posting date is startDate ("2026-11-06").
        posted_on = self.safe_text(data.get("postedOn"))
        posted_at = self.safe_datetime_iso(posted_on) if posted_on and not posted_on.startswith("Posted") else None
        if not posted_at:
            start = self.safe_text(data.get("startDate"))
            if len(start) == 10:
                posted_at = f"{start} 00:00:00"
            elif start:
                posted_at = self.safe_datetime_iso(start)

        # Apply URL — human-readable careers page. The detail's externalUrl is
        # the tenant's own canonical link (handles custom domains / locales);
        # fall back to rebuilding it from externalPath.
        # Workday's externalPath is usually just "/job/{Location}/{Title}_{JRID}" and
        # requires the "/en-US/{site_id}" prefix to resolve. Some tenants already
        # include "/en-US/{site_id}" in externalPath — detect and pass through.
        ext_path = self.safe_text(listing.get("externalPath"))
        base_domain = source["base_url"].split("/wday/")[0]
        site_id = source["base_url"].rstrip("/").rsplit("/", 1)[-1]
        external_url = data.get("externalUrl")
        if isinstance(external_url, str) and external_url.startswith("http"):
            apply_url = external_url
        elif not ext_path:
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
            description_html=data.get("jobDescription") if isinstance(data.get("jobDescription"), str) else "",
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
