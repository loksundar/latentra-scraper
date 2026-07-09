"""
Base spider class for all ATS job spiders.
Handles source loading, error handling, and common utilities.
"""

import json
from datetime import datetime, timezone

import scrapy

from latentra_scraper.sources.loader import get_active_sources


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
        self.logger.error(f"Request failed: {request.url} — {failure.value}")

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
    def safe_datetime_iso(value) -> str | None:
        """Parse an ISO datetime string into 'YYYY-MM-DD HH:MM:SS' or None."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def safe_datetime_epoch_ms(value) -> str | None:
        """Parse epoch milliseconds into 'YYYY-MM-DD HH:MM:SS' or None."""
        if not value:
            return None
        try:
            dt = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError, TypeError):
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
