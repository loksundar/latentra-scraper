"""
Rippling ATS spider.

    GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs
        → [{uuid, name, department{label}, url, workLocation{label}}]   (all jobs, no paging)
    GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uuid}
        → full posting: description{company, role} HTML, workLocations,
          employmentType{label, id}, createdOn, payRangeDetails, companyName

The listing has no description or date, so details are fetched — but only
for rows that pass the shared title/US prefilter.

Field shapes drift (label/id swapped, dicts turning into strings), so every
nested read goes through _label()/_dict().
"""

import scrapy

from latentra_scraper.items import JobItem
from latentra_scraper.spiders.base_job_spider import BaseJobSpider

API = "https://api.rippling.com/platform/api/ats/v1/board"


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _label(value, *keys) -> str:
    """Text from a field that may be a str, a dict with one of `keys`, or junk."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in keys or ("label", "name", "id"):
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


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
            # {"error_code": "RESOURCE_NOT_FOUND", ...} is a JSON body too.
            self.mark_source_failed(response, "Invalid JSON response (not a jobs list)")
            return
        self.logger.info(f"[{slug}] Found {len(jobs)} jobs")

        for job in jobs:
            if not isinstance(job, dict):
                continue
            uuid = job.get("uuid")
            if not uuid:
                continue
            location = _label(job.get("workLocation"), "label", "id")
            if self.skip_from_listing(_label(job.get("name")), location):
                self.count_prefiltered()
                continue
            yield scrapy.Request(
                f"{API}/{slug}/jobs/{uuid}",
                callback=self.parse_detail,
                cb_kwargs={"source": source, "listing": job},
                errback=self.handle_error,
            )

    @staticmethod
    def _employment(value) -> str:
        """employmentType is {label: "SALARIED_FT", id: "Salaried, full-time"} —
        or the other way round, or a plain string. Read both halves."""
        if isinstance(value, dict):
            raw = " ".join(str(v) for v in (value.get("label"), value.get("id")) if isinstance(v, str))
        else:
            raw = str(value or "")
        raw = raw.upper()
        if "INTERN" in raw:
            return "intern"
        if "_FT" in raw or "FULL" in raw:
            return "full_time"
        if "_PT" in raw or "PART" in raw:
            return "part_time"
        if "CONTRACT" in raw or "TEMP" in raw:
            return "contract"
        return "unknown"

    def parse_detail(self, response, source, listing):
        d = self.safe_json_parse(response.text)
        listing = _dict(listing)
        if not isinstance(d, dict) or not (d.get("uuid") or d.get("name")):
            # {"error_code": ...} is a dict too; without uuid/name there is no
            # posting here. The listed job would be missing from the batch and
            # swept as stale — flag the company's run incomplete instead.
            self.mark_source_failed(response, f"No detail for {listing.get('name', '?')} ({listing.get('uuid', '?')})")
            return

        desc = d.get("description")
        if isinstance(desc, dict):
            description_html = _label(desc, "role") + _label(desc, "company")
        elif isinstance(desc, str):
            description_html = desc
        else:
            description_html = ""

        locations = d.get("workLocations")
        if not isinstance(locations, list):
            locations = []
        location = "; ".join(l.strip() for l in locations if isinstance(l, str) and l.strip()) or \
            _label(listing.get("workLocation"), "label", "id")
        remote_type = "remote" if "remote" in location.lower() else "unknown"

        salary_min = salary_max = salary_currency = None
        pay_ranges = d.get("payRangeDetails")
        for pay in pay_ranges if isinstance(pay_ranges, list) else []:
            if not isinstance(pay, dict):
                continue
            if str(pay.get("frequency") or "").upper() != "YEAR":
                continue
            try:
                lo = int(float(pay.get("rangeStart") or 0))
                hi = int(float(pay.get("rangeEnd") or 0)) or None
            except (TypeError, ValueError):
                continue
            if lo > 0:
                salary_min, salary_max = lo, hi
                salary_currency = pay.get("currency") if isinstance(pay.get("currency"), str) else "USD"
                break

        department = _label(d.get("department"), "name", "label") or \
            _label(listing.get("department"), "label", "name") or None

        apply_url = d.get("url") if isinstance(d.get("url"), str) else ""
        if not apply_url and isinstance(listing.get("url"), str):
            apply_url = listing["url"]

        yield JobItem(
            source="rippling",
            source_job_id=str(d.get("uuid") or listing.get("uuid") or ""),
            title=self.safe_text(_label(d.get("name")) or _label(listing.get("name"))),
            company=source.get("company_name") or _label(d.get("companyName")) or self.company_display_name(source),
            company_slug=source["company_slug"],
            department=department,
            seniority="unknown",
            location=location,
            remote_type=remote_type,
            employment_type=self._employment(d.get("employmentType")),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            description_html=description_html,
            tags=None,
            apply_url=apply_url,
            posted_at=self.safe_datetime_iso(d.get("createdOn")),
        )
