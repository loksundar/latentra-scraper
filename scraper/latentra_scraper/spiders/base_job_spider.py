"""
Base spider class for all ATS job spiders.
Handles source loading, error handling, and common utilities.
"""

import json
from datetime import datetime, timedelta, timezone

import scrapy

from latentra_scraper.sources.loader import get_active_sources

_US_COUNTRY_CODES = {"US", "USA", "U.S", "U.S.A", "UNITED STATES", "UNITED STATES OF AMERICA"}

# The clock jobs-ingest.php / jobs-db.php run on (date_default_timezone_set
# and SET time_zone). Falls back to fixed CST on hosts without tzdata.
try:
    from zoneinfo import ZoneInfo
    _SERVER_TZ = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover - no tzdata on the host
    _SERVER_TZ = timezone(timedelta(hours=-6))


class BaseJobSpider(scrapy.Spider):
    """
    Subclasses must set:
        name            — spider name (e.g. 'greenhouse')
        source_type     — matches job_sources.source_type
    And implement:
        build_url(source) -> str
        parse_jobs(response, source) -> yields JobItem
    """

    source_type: str = ""

    async def start(self):
        """
        Scrapy 2.13+ entry point. Scrapy >= 2.15 no longer falls back to a
        custom start_requests() automatically (spiders silently yield zero
        requests) — delegate explicitly so both old and new versions work.
        """
        for request in self.start_requests():
            yield request

    def start_requests(self):
        """
        Load sources from the API and yield initial requests.
        Override build_requests() for custom request logic (e.g. POST-based APIs).
        """
        sources = get_active_sources(self.source_type)
        if not sources:
            self.logger.warning(f"No active sources for {self.source_type}")
            return

        for source in sources:
            yield from self.build_requests(source)

    def build_requests(self, source: dict):
        """
        Yield one or more initial scrapy.Request for this source.
        Default: single GET to build_url(). Override for POST-based APIs.
        """
        url = self.build_url(source)
        yield scrapy.Request(
            url,
            callback=self.parse_jobs,
            cb_kwargs={"source": source},
            errback=self.handle_error,
        )

    def build_url(self, source: dict) -> str:
        """Override in subclass. Return the API URL for this source."""
        raise NotImplementedError

    def parse_jobs(self, response, source: dict):
        """Override in subclass. Yield JobItem instances."""
        raise NotImplementedError

    def handle_error(self, failure):
        request = failure.request
        from latentra_scraper.middlewares import mark_failed
        mark_failed(self, request)
        self.logger.error(f"Request failed: {request.url} — {failure.value}")

    def mark_source_failed(self, response, why: str = ""):
        """
        Record that this company's crawl is NOT trustworthy this run and log why.

        Call it from any callback branch that gives up on a 2xx body (JSON that
        isn't the listing we expected: an error object, a missing container key,
        a WAF/interstitial page). Returning silently from such a branch would
        let CompanyReachMiddleware count the company as "checked cleanly", and
        the ingest pipeline would then heartbeat it (or sweep its jobs as
        stale) on the strength of a broken response.
        """
        from latentra_scraper.middlewares import mark_failed
        request = getattr(response, "request", None) or response
        mark_failed(self, request)
        if why:
            self.logger.error(f"[{self._slug_of(request)}] {why}")

    def mark_source_incomplete(self, response, why: str = ""):
        """
        Record that this run saw only PART of the company's postings, without
        calling the ATS broken: the spider stopped paging at its own cap
        (MAX_PAGES / MAX_OFFSET) while the API still had rows. The ingest
        pipeline then sends the batch with partial=1 so the server does not
        sweep the postings beyond the cap as stale.
        """
        from latentra_scraper.middlewares import mark_incomplete
        request = getattr(response, "request", None) or response
        mark_incomplete(self, request)
        if why:
            self.logger.warning(f"[{self._slug_of(request)}] {why} — batch will be ingested as partial")

    def _slug_of(self, request) -> str:
        src = (getattr(request, "cb_kwargs", None) or {}).get("source") or {}
        slug = src.get("company_slug") if isinstance(src, dict) else None
        return slug or self.name

    @staticmethod
    def is_non_us_country(value) -> bool:
        """
        True when an ATS-supplied country code/name is clearly NOT the United
        States ("GB", "Canada", "Greece"). Empty / non-string / unknown values
        return False so the location-text heuristics still get their say.
        """
        if not isinstance(value, str):
            return False
        v = value.strip().upper().rstrip(".")
        if not v:
            return False
        return v not in _US_COUNTRY_CODES

    # ── Listing prefilter ────────────────────────────
    # For ATSs that need one detail request per job (Workday, Rippling,
    # Oracle), apply ValidatePipeline's title and US rules to the listing row
    # first and skip details that would be dropped anyway.

    def skip_from_listing(self, title: str, location: str = "", country: str = "") -> bool:
        from latentra_scraper.pipelines.validate import (
            IT_TITLE_PATTERNS,
            NON_IT_TITLE_PATTERNS,
            ValidatePipeline,
        )
        title = title if isinstance(title, str) else ""
        location = location if isinstance(location, str) else ""
        if NON_IT_TITLE_PATTERNS.search(title) and not IT_TITLE_PATTERNS.search(title):
            return True
        if self.is_non_us_country(country):
            return True
        checker = getattr(self, "_us_checker", None)
        if checker is None:
            checker = self._us_checker = ValidatePipeline()
        if not checker._is_likely_us({"location": location or ""}):
            return True
        return False

    def skip_old_from_listing(self, posted) -> bool:
        """
        True when a listing row's own date already fails ValidatePipeline's
        MAX_JOB_AGE_DAYS rule, so its detail fetch would be wasted. Accepts ISO
        datetimes ("2017-09-12T21:27:42.000Z") or plain dates ("2026-11-07");
        unparseable / missing dates return False (fetch and let the validator
        decide). Seen live: a SmartRecruiters tenant with 1,342 postings from
        2017 cost 767 detail requests that were all dropped as too old.
        """
        from latentra_scraper.pipelines.validate import ValidatePipeline
        if not isinstance(posted, str) or not posted.strip():
            return False
        iso = self.safe_datetime_iso(posted) if len(posted.strip()) > 10 else f"{posted.strip()} 00:00:00"
        if not iso:
            return False
        checker = getattr(self, "_us_checker", None)
        if checker is None:
            checker = self._us_checker = ValidatePipeline()
        return checker._is_too_old(iso)

    def count_prefiltered(self):
        if getattr(self, "crawler", None):
            self.crawler.stats.inc_value(f"{self.source_type}/listing_prefiltered")

    # ── Utility methods ──────────────────────────────

    @staticmethod
    def safe_text(value, default="") -> str:
        """Safely extract a string value."""
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    @staticmethod
    def _to_server_clock(dt: datetime) -> str:
        """
        Format for the DATETIME column. jobs-ingest.php stamps first_seen_at /
        last_seen_at in America/Chicago, so a timezone-aware posting date is
        converted to that clock before the offset is dropped; otherwise a job
        published 03:00 UTC lands as "posted 03:00" next to a first_seen_at of
        22:30 the previous day. Naive values are passed through unchanged.
        """
        if dt.tzinfo is not None:
            dt = dt.astimezone(_SERVER_TZ)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def safe_datetime_iso(value) -> str | None:
        """Parse an ISO datetime string into 'YYYY-MM-DD HH:MM:SS' (server clock) or None."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            return BaseJobSpider._to_server_clock(dt)
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def safe_datetime_epoch_ms(value) -> str | None:
        """Parse epoch milliseconds into 'YYYY-MM-DD HH:MM:SS' (server clock) or None."""
        if not value:
            return None
        try:
            dt = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
            return BaseJobSpider._to_server_clock(dt)
        except (ValueError, OSError, TypeError, OverflowError):
            return None

    @staticmethod
    def safe_json_parse(text: str, fallback=None):
        """Parse JSON safely, return fallback on failure."""
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return fallback

    @staticmethod
    def company_display_name(source: dict) -> str:
        """Get display name from source, falling back to slug titlecase."""
        return source.get("company_name") or source["company_slug"].replace("-", " ").title()
