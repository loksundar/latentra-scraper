"""
Apple careers spider (single company).

    GET  https://jobs.apple.com/api/v1/CSRFToken  → x-apple-csrf-token header
    POST https://jobs.apple.com/api/v1/search     (20/page, US filter, newest first)

Items are built straight from search rows (jobSummary, ~600 chars median).
Per-job detail calls were dropped after live runs on 2026-11-06: Apple
serves ~80 req/min, so ~4.5k detail fetches can't fit the crawl budget, and
fanning out search pages in parallel invalidated the CSRF session (detail
responses came back empty). ~230 sequential search requests cover all US jobs.
"""

import json

import scrapy

from latentra_scraper.items import JobItem
from latentra_scraper.spiders.base_job_spider import BaseJobSpider

BASE = "https://jobs.apple.com"
PAGE_SIZE = 20
SKIP_TEAMS = {"apple retail", "applecare", "support and service",
              "sales and business development", "marketing"}


class AppleSpider(BaseJobSpider):
    name = "apple"
    source_type = "apple"
    allowed_domains = ["jobs.apple.com"]

    MAX_PAGES = 300

    def build_requests(self, source):
        # The token endpoint answers 200 with an EMPTY body (the token is a
        # header) — tell CompanyReachMiddleware not to treat that as a WAF page.
        yield scrapy.Request(
            f"{BASE}/api/v1/CSRFToken",
            callback=self.got_token,
            cb_kwargs={"source": source, "expect_json": False},
            errback=self.handle_error,
            dont_filter=True,
        )

    def got_token(self, response, source, expect_json=False):
        token = (response.headers.get(b"x-apple-csrf-token") or b"").decode()
        if not token:
            self.mark_source_failed(response, "No CSRF token — search cannot run")
            return
        yield self._search(source, token, 1)

    def _search(self, source, token, page):
        body = {
            "query": "",
            "filters": {"range": {"standardWeeklyHours": {"start": None, "end": None}},
                        "locations": ["postLocation-USA"]},
            "page": page, "locale": "en-us", "sort": "newest",
            "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"},
        }
        return scrapy.Request(
            f"{BASE}/api/v1/search", method="POST", body=json.dumps(body),
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "x-apple-csrf-token": token, "Origin": BASE, "Referer": f"{BASE}/en-us/search"},
            callback=self.parse_search, cb_kwargs={"source": source, "token": token, "page": page},
            errback=self.handle_error, dont_filter=True,
        )

    def parse_search(self, response, source, token, page):
        data = self.safe_json_parse(response.text)
        res = data.get("res") if isinstance(data, dict) else None
        # An expired CSRF session answers 200 with {} / no "res" — that is a
        # failed search, never "Apple has 0 jobs".
        if not isinstance(res, dict) or not isinstance(res.get("searchResults"), list):
            self.mark_source_failed(response, f"Search page {page} returned no results block")
            return
        results = [r for r in res["searchResults"] if isinstance(r, dict)]
        total = res.get("totalRecords") if isinstance(res.get("totalRecords"), int) else 0
        if page == 1:
            self.logger.info(f"[apple] Total jobs: {total}")
        # The token may rotate; prefer the freshest one the server hands back.
        token = (response.headers.get(b"x-apple-csrf-token") or b"").decode() or token

        for row in results:
            item = self.row_to_item(row, source)
            if item is None:
                self.count_prefiltered()
                continue
            yield item

        if results and page * PAGE_SIZE < total:
            if page >= self.MAX_PAGES:
                self.mark_source_incomplete(response, f"paging cap at page {page} ({total} results)")
                return
            yield self._search(source, token, page + 1)

    def row_to_item(self, row, source):
        team_obj = row.get("team")
        team = self.safe_text(team_obj.get("teamName") if isinstance(team_obj, dict) else team_obj)
        locs = row.get("locations")
        loc = locs[0] if isinstance(locs, list) and locs and isinstance(locs[0], dict) else {}
        place = self.safe_text(loc.get("city")) or self.safe_text(loc.get("name"))  # city is often empty
        parts = [p for p in (place, self.safe_text(loc.get("stateProvince"))) if p]
        location = ", ".join(parts + ["United States"]) if parts else "United States"
        title = self.safe_text(row.get("postingTitle"))
        if team.lower() in SKIP_TEAMS or self.skip_from_listing(title, location):
            return None

        pid = row.get("positionId") or row.get("id")
        summary = row.get("jobSummary") if isinstance(row.get("jobSummary"), str) else ""
        return JobItem(
            source="apple",
            source_job_id=str(row.get("id") or pid),
            title=self.safe_text(title),
            company=source.get("company_name") or "Apple",
            company_slug=source["company_slug"],
            department=team or None,
            seniority="unknown",
            location=location,
            remote_type="remote" if row.get("homeOffice") else "unknown",
            employment_type="full_time" if str(row.get("standardWeeklyHours")) == "40" else "unknown",
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            description_html="<p>" + summary.replace("\n", "<br>") + "</p>" if summary else "",
            tags=None,
            apply_url=f"{BASE}/en-us/details/{pid}/{row.get('transformedPostingTitle') or ''}",
            posted_at=self.safe_datetime_iso(row["postDateInGMT"][:19] + "+00:00")
                      if isinstance(row.get("postDateInGMT"), str) and row["postDateInGMT"] else None,
        )
