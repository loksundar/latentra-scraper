"""
Oracle Recruiting Cloud (Oracle HCM "Candidate Experience") spider.

Used by many large US employers (banks, industrials, Taleo migrations).
Each source's base_url is the careers site root, e.g.
    https://ibqbjb.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/Honeywell

Listing (25/page, newest first):
    GET {host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
        ?onlyData=true&expand=requisitionList
        &finder=findReqs;siteNumber={site},limit=25,offset={n},sortBy=POSTING_DATES_DESC
Detail:
    GET {host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails
        ?expand=all&onlyData=true&finder=ById;Id="{id}",siteNumber={site}

Listing rows carry PrimaryLocationCountry, so non-US and non-IT rows are
skipped before the detail request (big tenants list thousands of jobs).
"""

import re

import scrapy

from latentra_scraper.items import JobItem
from latentra_scraper.spiders.base_job_spider import BaseJobSpider

_SITE_RE = re.compile(r"^(https?://[^/]+)/hcmUI/CandidateExperience/[^/]+/sites/([^/?#]+)", re.I)


def parse_base_url(base_url: str):
    m = _SITE_RE.match(base_url or "")
    return (m.group(1), m.group(2)) if m else (None, None)


class OracleSpider(BaseJobSpider):
    name = "oracle"
    source_type = "oracle"

    PAGE_SIZE = 25
    MAX_PAGES = 80  # 2,000 newest postings per tenant is plenty

    def _list_url(self, host, site, offset):
        return (
            f"{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            f"?onlyData=true&expand=requisitionList"
            f"&finder=findReqs;siteNumber={site},limit={self.PAGE_SIZE},offset={offset},sortBy=POSTING_DATES_DESC"
        )

    def build_requests(self, source):
        host, site = parse_base_url(source.get("base_url"))
        if not host:
            self.logger.error(f"[{source['company_slug']}] Bad Oracle base_url: {source.get('base_url')}")
            return
        yield scrapy.Request(
            self._list_url(host, site, 0),
            headers={"Accept": "application/json"},
            callback=self.parse_listing,
            cb_kwargs={"source": source, "host": host, "site": site, "offset": 0},
            errback=self.handle_error,
        )

    def parse_listing(self, response, source, host, site, offset):
        data = self.safe_json_parse(response.text)
        slug = source["company_slug"]
        items = data.get("items") if isinstance(data, dict) else None
        # The finder always returns one search block (TotalJobsCount may be 0);
        # no block means an error payload, not an empty site.
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            self.mark_source_failed(response, "Invalid Oracle listing response")
            return
        block = items[0]
        total = block.get("TotalJobsCount")
        if not isinstance(total, int):
            total = 0
        reqs = block.get("requisitionList")
        if not isinstance(reqs, list):
            reqs = []
        if offset == 0:
            self.logger.info(f"[{slug}] Total jobs: {total}")

        all_old = bool(reqs)   # listing is newest-first: a page of only-old rows ends paging
        dates = []
        for req in reqs:
            if not isinstance(req, dict):
                continue
            req_id = req.get("Id")
            if not req_id:
                continue
            if isinstance(req.get("PostedDate"), str):
                dates.append(req["PostedDate"])
            if self.skip_old_from_listing(req.get("PostedDate")):
                self.count_prefiltered()
                continue
            all_old = False
            if self.skip_from_listing(req.get("Title"), req.get("PrimaryLocation"),
                                      req.get("PrimaryLocationCountry") or ""):
                self.count_prefiltered()
                continue
            yield scrapy.Request(
                f"{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
                f'?expand=all&onlyData=true&finder=ById;Id="{req_id}",siteNumber={site}',
                headers={"Accept": "application/json"},
                callback=self.parse_detail,
                cb_kwargs={"source": source, "host": host, "site": site, "listing": req},
                errback=self.handle_error,
            )

        # The all-old early stop is only safe if the tenant honoured
        # sortBy=POSTING_DATES_DESC; an unsorted page that happens to be all old
        # says nothing about the pages after it, so keep paging in that case.
        if all_old and dates != sorted(dates, reverse=True):
            all_old = False
        next_offset = offset + self.PAGE_SIZE
        if reqs and not all_old and next_offset < total:
            if next_offset >= self.PAGE_SIZE * self.MAX_PAGES:
                self.mark_source_incomplete(response, f"paging cap reached at offset {next_offset} (total={total})")
                return
            yield scrapy.Request(
                self._list_url(host, site, next_offset),
                headers={"Accept": "application/json"},
                callback=self.parse_listing,
                cb_kwargs={"source": source, "host": host, "site": site, "offset": next_offset},
                errback=self.handle_error,
            )

    def parse_detail(self, response, source, host, site, listing):
        data = self.safe_json_parse(response.text)
        listing = listing if isinstance(listing, dict) else {}
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            # The job is listed but we could not build it → it would be missing
            # from the batch → the server would sweep it. Flag the run instead.
            self.mark_source_failed(response, f"No detail for {listing.get('Title', '?')} ({listing.get('Id', '?')})")
            return
        d = items[0]

        description_html = "".join(
            d.get(k) for k in (
                "ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                "ExternalQualificationsStr", "CorporateDescriptionStr",
            ) if isinstance(d.get(k), str)
        )

        workplace = self.safe_text(d.get("WorkplaceType") or listing.get("WorkplaceTypeCode")).lower()
        remote_type = ("remote" if "remote" in workplace else
                       "hybrid" if "hybrid" in workplace else
                       "onsite" if "site" in workplace else "unknown")

        schedule = self.safe_text(d.get("JobSchedule")).lower()
        employment_type = ("full_time" if "full" in schedule else
                           "part_time" if "part" in schedule else "unknown")

        req_id = d.get("Id") or listing.get("Id")
        yield JobItem(
            source="oracle",
            source_job_id=str(req_id),
            title=self.safe_text(d.get("Title") or listing.get("Title")),
            company=self.company_display_name(source),
            company_slug=source["company_slug"],
            department=self.safe_text(d.get("Organization") or d.get("Department") or listing.get("JobFamily")) or None,
            seniority="unknown",
            location=self.safe_text(d.get("PrimaryLocation") or listing.get("PrimaryLocation")),
            remote_type=remote_type,
            employment_type=employment_type,
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            description_html=description_html,
            tags=None,
            apply_url=f"{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{req_id}",
            posted_at=self.safe_datetime_iso(d.get("ExternalPostedStartDate"))
                      or (f"{listing['PostedDate']} 00:00:00" if listing.get("PostedDate") else None),
        )
